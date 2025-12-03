import contextlib
import os
import time
import json
import shutil 
import threading
import queue
import heapq
from typing import Iterator, Tuple,List,Dict,Any
import csv
import msgpack
import concurrent.futures

from ..abstract_kv_store import AbstractKVStore
from .wal import WriteAheadLog, TOMBSTONE 
from .memtable import ShardedMemtable
from .sstable import SSTableManager, TOMBSTONE_VALUE 

class LSMCompactionError(RuntimeError):
    """Raised for non-IO critical errors during LSM compaction flow."""
    pass

class LSMTreeStore(AbstractKVStore):
    MANIFEST_FILE = "MANIFEST"
    WAL_FILE = "wal.log"
    SSTABLES_SUBDIR = "sstables" 
    # Default Compaction Triggers
    DEFAULT_MEMTABLE_THRESHOLD_BYTES = 4 * 1024 * 1024 # 4MB
    DEFAULT_MAX_L0_SSTABLES = 4 # Trigger L0->L1 compaction
    COMPACTION_RATIO_T = 10
    LEVELED_CAPACITY_PROXY = 10
    ENGINE_META_FILE = "engine.meta"

    def __init__(self, collection_path: str, options: dict = None):
        super().__init__(collection_path, options)

        self.wal_path = os.path.join(self.collection_path, self.WAL_FILE)
        self.sstables_storage_dir = os.path.join(self.collection_path, self.SSTABLES_SUBDIR)
        self.manifest_path = os.path.join(self.collection_path, self.MANIFEST_FILE)

        # Ensure sstables directory exists
        os.makedirs(self.sstables_storage_dir, exist_ok=True)

        # Initialize objects
        self.wal: WriteAheadLog | None = None
        self.memtable: ShardedMemtable | None = None
        self.sstable_manager: SSTableManager = SSTableManager(self.sstables_storage_dir)
        
        self.levels: list[list[str]] = [] 
        self._level_lock = threading.Lock() # CRITICAL: Threading Lock for `self.levels` and `MANIFEST` file access
        self._metadata_lock = threading.Lock()
        self._flush_lock = threading.RLock()

        self._compaction_queue = queue.Queue()
        self._compaction_stop_event = threading.Event()
        self._compaction_thread: threading.Thread | None = None

        # Apply options
        current_options = options if options is not None else {}
        self.memtable_flush_threshold_bytes = current_options.get(
            "memtable_threshold_bytes", self.DEFAULT_MEMTABLE_THRESHOLD_BYTES
        )
        self.max_l0_sstables_before_compaction = current_options.get(
            "max_l0_sstables", self.DEFAULT_MAX_L0_SSTABLES
        )
        self.level_size_ratio = current_options.get(
            "compaction_ratio", self.COMPACTION_RATIO_T
        )
        self._key_count: int = 0

    def _process_and_write_chunk(self, chunk_data: List[Tuple[str, bytes]], sstable_id: str) -> str:
        """
        Helper executed by the ThreadPoolExecutor to write a single sorted chunk 
        to a set of SSTable files.
        """
        if not chunk_data:
            raise ValueError("Attempted to process an empty chunk.")

        # 2. Write SSTable
        try:
            if not self.sstable_manager.write_sstable(sstable_id, chunk_data):
                raise IOError(f"Write operation returned False for SSTable {sstable_id}.")
            return sstable_id
        except IOError as e:
            # If the write fails, attempt to clean up any partial files immediately
            self.sstable_manager.delete_sstable_files(sstable_id)
            # Re-raise to signal failure to the ThreadPoolExecutor
            raise IOError(f"Failed to write SSTable for chunk {sstable_id}: {e}")
        
    def _get_meta_file_path(self) -> str: 
        """Gets the path to the collection's metadata file."""
        return os.path.join(self.collection_path, self.ENGINE_META_FILE)
    
    @property
    def key_count(self) -> int:
        return self._key_count

    def update_metadata(self) -> None:
        """Implements abstract method: reads, updates, and writes the key count."""
        meta_file_path = self._get_meta_file_path()
        with self._metadata_lock:
            try:
                with open(meta_file_path, 'r', encoding='utf-8') as f:
                    meta_data = json.load(f)
                meta_data["kv_pair_count"] = self._key_count        
                with open(meta_file_path, 'w', encoding='utf-8') as f:
                    json.dump(meta_data, f, indent=2)
            except Exception as e:
                print(f"Warning: Failed to update metadata file {meta_file_path}: {e}")

    def _generate_sstable_id(self) -> str:
        return f"sst_{int(time.time() * 1000000)}_{len(self.sstable_manager.get_all_sstable_ids_from_disk())}"


    def _write_manifest(self) -> bool:
        """
        Note: This method is called from inside a lock (self._level_lock) 
        in all callers (_flush_memtable and _compact_level).
        """
        try:
            with open(self.manifest_path, 'w', encoding='utf-8') as f:
                json.dump({"levels": self.levels}, f, indent=2)
            return True
        except IOError as e:
            raise IOError(f"CRITICAL: Error writing MANIFEST file {self.manifest_path}: {e}")

    def _load_manifest(self) -> bool:
        if not os.path.exists(self.manifest_path):
            self.levels = []
            return

        try:
            with open(self.manifest_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            loaded_levels = data.get("levels", [])
            
            if isinstance(loaded_levels, list) and all(isinstance(level, list) for level in loaded_levels):
                self.levels = loaded_levels
            else:
                self.levels = [] # Treat invalid format as a new store
        except (IOError, json.JSONDecodeError) as e:
            raise IOError(f"Error reading or parsing MANIFEST {self.manifest_path}: {e}") 


    def load(self) -> None:
        self._load_manifest()
        self.wal = WriteAheadLog(self.wal_path)
        self.memtable = ShardedMemtable(threshold_bytes=self.memtable_flush_threshold_bytes)
        meta_file_path = self._get_meta_file_path()
        if os.path.exists(meta_file_path):
            try:
                with open(meta_file_path, 'r', encoding='utf-8') as f:
                    meta_data = json.load(f)
                self._key_count = meta_data.get("kv_pair_count", 0)
            except Exception:
                self._key_count = 0
        wal_entries = self.wal.replay() # WAL replay should handle its own errors gracefully
        if wal_entries:
            for entry in wal_entries:
                op_type = entry.get("op")
                key = entry.get("key")
                if op_type == "PUT":
                    value = entry.get("value")
                    self.memtable.put(key, value)
                elif op_type == "DELETE":
                    self.memtable.delete(key) # Internally uses TOMBSTONE
        
        # start compaction thread
        self._compaction_thread = threading.Thread(target=self._compaction_worker_run, daemon=True)
        self._compaction_thread.start()

    def put(self, key: str, value: str) -> None:
        if self.wal is None or self.memtable is None:
            raise RuntimeError("LSMTreeStore is not properly loaded")
        
        shard_idx = self.memtable.get_shard_index(key)
        with self.memtable.locks[shard_idx]:
            exists_in_shard = self.memtable.shards[shard_idx].get(key)
            if exists_in_shard is not None:
                exists_before_put = (exists_in_shard != TOMBSTONE)
            else:
                exists_before_put = self._exists_on_disk(key)
            
            self.wal.log_operation("PUT", key, value)
            self.memtable.put_to_shard(shard_idx, key, value)
            if not exists_before_put and value is not TOMBSTONE: 
                with self._metadata_lock:
                    self._key_count += 1
        
        if self.memtable.is_full():
            self._flush_memtable()
            self.update_metadata()

    def delete(self, key: str) -> None:
        if self.wal is None or self.memtable is None:
            raise RuntimeError("LSMTreeStore not properly loaded. Call load() via StorageManager.")

        shard_idx = self.memtable.get_shard_index(key)
        with self.memtable.locks[shard_idx]:
            exists_in_shard = self.memtable.shards[shard_idx].get(key)
            if exists_in_shard is not None:
                exists_before_delete = (exists_in_shard != TOMBSTONE)
            else:
                exists_before_delete = self._exists_on_disk(key)
            self.wal.log_operation("DELETE", key)
            self.memtable.put_to_shard(shard_idx, key, TOMBSTONE)
            if exists_before_delete:
                with self._metadata_lock:
                    self._key_count = max(0, self._key_count - 1)
        
        if self.memtable.is_full():
             self._flush_memtable()
             self.update_metadata()
    
    def _exists_on_disk(self, key: str) -> bool:
        """Helper to check existence only in SSTables (skipping memtable)."""
        # Search SSTables
        for level_idx, sstable_ids_in_level in enumerate(self.levels):
            search_order = reversed(sstable_ids_in_level) if level_idx == 0 else sstable_ids_in_level
            for sstable_id in search_order:
                if not self.sstable_manager.check_bloom_filter(sstable_id, key):
                    continue 
                sstable_val, was_tombstone_str = self.sstable_manager.find_in_sstable(sstable_id, key)
                if sstable_val is not None:
                    return not was_tombstone_str
        return False
    
    def get(self, key: str) -> str | None:
        if self.memtable is None: # Check memtable existence as a proxy for loaded state
             raise RuntimeError("LSMTreeStore not properly loaded. Call load() via StorageManager.")

        mem_value = self.memtable.get(key) #hashes the key and gets lock on the right shard internally
        if mem_value is not None:
            return None if mem_value is TOMBSTONE else mem_value

        for level_idx, sstable_ids_in_level in enumerate(self.levels):
            search_order = reversed(sstable_ids_in_level) if level_idx == 0 else sstable_ids_in_level
            
            for sstable_id in search_order:
                if not self.sstable_manager.check_bloom_filter(sstable_id, key):
                    continue 
                sstable_val, was_tombstone_str = self.sstable_manager.find_in_sstable(sstable_id, key)
                
                if sstable_val is not None: 
                    if was_tombstone_str or sstable_val == TOMBSTONE_VALUE:
                        return None 
                    return sstable_val
        return None

    def _memtable_range_iterator(self, start_key: str, end_key: str) -> Iterator[Tuple[str, str]]:
        """
        Generates a range iterator for the Sharded Memtable [start_key, end_key].
        Captures a consistent snapshot across all shards.
        """
        if self.memtable is None:
            return

        # 1. Lock ALL shards to ensure we get a consistent "point-in-time" snapshot
        with contextlib.ExitStack() as stack:
            for lock in self.memtable.locks:
                stack.enter_context(lock)
            
            # 2. Merge sorted items from all shards into one list
            # Note: This is efficient because it uses heapq.merge on already sorted shards
            all_items = self.memtable.get_sorted_items()

        for key, value in all_items:
            if key >= end_key:
                break
            
            if key >= start_key:
                value_to_yield = TOMBSTONE_VALUE if value is TOMBSTONE else value 
                yield (key, value_to_yield)

    def range_query(self, start_key: str, end_key: str) -> Iterator[Tuple[str, str]]:
        """
        Performs a k-way merge (Heap Merge Iterator) across all levels and the Memtable
        to return all key-value pairs in a sorted range [start_key, end_key], 
        ensuring only the newest version of each key is returned.

        """
        if self.memtable is None:
             raise RuntimeError("LSMTreeStore is not properly loaded")

        heap = []
        source_id_counter = 0 # Used to differentiate iterators; lower ID means newer source/level

        memtable_it = self._memtable_range_iterator(start_key,end_key)
        try:
            key, value = next(memtable_it)
            heapq.heappush(heap, (key, source_id_counter, value, memtable_it))
        except StopIteration:
            pass
        source_id_counter += 1

        with self._level_lock:
            for level_idx, sstable_ids_in_level in enumerate(self.levels):
                for sstable_id in sstable_ids_in_level:
                    range_info = self.sstable_manager.get_sstable_key_range(sstable_id)
                    if range_info:
                        meta_min, meta_max = range_info
                        if start_key > meta_max or end_key <= meta_min:
                             continue
                    
                    sstable_it = self.sstable_manager.range_iterator(sstable_id, start_key, end_key)
                    try:
                        key, value = next(sstable_it)
                        heapq.heappush(heap, (key, source_id_counter, value, sstable_it))
                        source_id_counter += 1
                    except StopIteration:
                        continue
        
        last_key = None
        latest_value = None
        
        while heap:
            key, _, value, iterator = heapq.heappop(heap)
            if key != last_key:
                if last_key is not None:
                    if latest_value != TOMBSTONE_VALUE and latest_value is not None:
                        yield (last_key, latest_value)
                last_key = key
                latest_value = value
            try:
                next_key, next_value = next(iterator)
                heapq.heappush(heap, (next_key, source_id_counter, next_value, iterator))
            except StopIteration:
                pass

        if last_key is not None and latest_value != TOMBSTONE_VALUE and latest_value is not None:
             yield (last_key, latest_value)
        
    def exists(self, key: str) -> bool:
        return self.get(key) is not None


    def _flush_memtable(self) -> None:
        if not self.memtable or not self.wal or len(self.memtable) == 0:
            return

        with self._flush_lock: #obtain lock on all shards
            with contextlib.ExitStack() as stack:
                for lock in self.memtable.locks:
                    stack.enter_context(lock)

                if len(self.memtable) == 0:
                    return 

                sorted_items = [(k, v if v is not TOMBSTONE else TOMBSTONE_VALUE) 
                                for k, v in self.memtable.get_sorted_items()]
                
                sstable_id = self._generate_sstable_id()
                if self.sstable_manager.write_sstable(sstable_id, sorted_items):
                    with self._level_lock:
                        if not self.levels:
                            self.levels.append([])
                        self.levels[0].append(sstable_id)
                        
                        try:
                            self._write_manifest()
                        except IOError as e:
                            self.levels[0].remove(sstable_id)
                            self.sstable_manager.delete_sstable_files(sstable_id)
                            raise e
                    
                    self.memtable.clear()
                    self.wal.truncate()

                    if self._compaction_thread and self._compaction_thread.is_alive():
                        self._compaction_queue.put(("FLUSH_COMPLETE", 0))
                
                else:
                    raise IOError(f"CRITICAL: Failed to write SSTable {sstable_id}.")
                
    def _compaction_worker_run(self):
        """Dedicated thread function for handling compaction tasks."""
        while not self._compaction_stop_event.is_set():
            try:
                task_type, level_idx = self._compaction_queue.get(timeout=1) 
            except queue.Empty:
                continue # Continue loop if queue is empty
            
            try:
                if task_type == "FLUSH_COMPLETE":
                    self._check_and_trigger_compaction()
            except Exception as e:
                print(f"CRITICAL COMPACTION ERROR: Compaction failed in background thread: {type(e).__name__}: {e}") 
            finally:
                self._compaction_queue.task_done()

    def _check_and_trigger_compaction(self):
        """
        Checks for and triggers the next required compaction task.
        Called by the background compaction thread.
        """
        level_to_compact = -1
        
        with self._level_lock: # Protect reading self.levels state
            if not self.levels or not self.levels[0]: # No L0 SSTables
                return

            # 1. Simple L0 to L1 compaction (Tiered Compaction)
            if len(self.levels[0]) >= self.max_l0_sstables_before_compaction:
                level_to_compact = 0
            
            # 2. L1+ to L(i+1) compaction (Leveled/Partial Compaction)
            # Only check L1+ if L0 compaction is not required.
            if level_to_compact < 0: 
                for level_idx in range(1, len(self.levels)):
                    if len(self.levels[level_idx]) > self.LEVELED_CAPACITY_PROXY:
                        level_to_compact = level_idx
                        break
        
        # Call the compaction method outside the lock (it's I/O bound)
        if level_to_compact >= 0:
            self._compact_level(level_to_compact)
            return

    def _compact_level(self, level_idx: int):
        """
        Compacts SSTables within a given level or from this level to the next.
        Uses Tiered compaction for L0->L1 and Leveled compaction for L1+->L2+.
        """
        if level_idx < 0 or level_idx >= len(self.levels) or not self.levels[level_idx]:
            return

        # Ensure target level exists
        target_level_idx = level_idx + 1
        with self._level_lock: # Need lock for reading and ensuring target level exists/extends
            if target_level_idx >= len(self.levels): # Ensure target level list exists
                self.levels.extend([[] for _ in range(target_level_idx - len(self.levels) + 1)])
        
        all_input_sstables_for_compaction: list[str] = []
        output_sstable_id = self._generate_sstable_id() 
        
        # --- L0 -> L1 Compaction (Tiered/Full Merge) ---
        if level_idx == 0:
            with self._level_lock: # Need lock for reading L0 files
                all_input_sstables_for_compaction = list(self.levels[0])

        # --- L1+ -> L2+ Compaction (Leveled/Partial Merge) ---
        else: 
            
            # 1. Select the oldest/smallest SSTable from the source level (L(i))
            with self._level_lock: # Need lock for reading L(i) files
                sstable_to_compact = self.levels[level_idx][0] # Always choose the oldest/first file
            
            range_info = self.sstable_manager.get_sstable_key_range(sstable_to_compact)
            if not range_info:
                raise LSMCompactionError(f"CRITICAL: Failed to get key range for L{level_idx} sstable {sstable_to_compact}. Compaction aborted.")
            
            source_min_key, source_max_key = range_info

            # Collect files for merging
            sstables_to_merge = [sstable_to_compact]
            target_sstables_to_remove = []
            
            # 2. Find ALL overlapping SSTables in the target level (L(i+1))
            with self._level_lock: # Need lock for reading L(i+1) files
                for target_id in list(self.levels[target_level_idx]): 
                    target_range_info = self.sstable_manager.get_sstable_key_range(target_id)
                    if not target_range_info:
                        raise LSMCompactionError(f"CRITICAL: Missing range info for target sstable {target_id} in L{target_level_idx}. Data integrity issue.")
                    target_min, target_max = target_range_info
                    
                    # Check for key range overlap:
                    # Overlap occurs if (SourceMin <= TargetMax) AND (SourceMax >= TargetMin)
                    if source_min_key <= target_max and source_max_key >= target_min:
                        sstables_to_merge.append(target_id)
                        target_sstables_to_remove.append(target_id)

            all_input_sstables_for_compaction = sstables_to_merge
            
        # --- Execute Merge, Update Manifest, and Cleanup (Common Logic) ---
        if not all_input_sstables_for_compaction:
            return

        # Perform the actual k-way merge (I/O heavy - MUST be outside the lock)
        if self.sstable_manager.compact_sstables(all_input_sstables_for_compaction, output_sstable_id):
            
            with self._level_lock: # Protect shared state updates
                # 1. Remove old files from source level (L(i))
                if level_idx == 0:
                    self.levels[level_idx] = [] # Clear ALL of L0 (Tiered)
                else: # L1+ (Leveled)
                    self.levels[level_idx].remove(sstable_to_compact)
                    
                # 2. Remove old files from target level (L(i+1))
                if level_idx > 0: # Only applies to Leveled compactions
                    for target_id in target_sstables_to_remove:
                        self.levels[target_level_idx].remove(target_id)
                
                # 3. Add the new SSTable to the target level.
                self.levels[target_level_idx].append(output_sstable_id)
                
                try:
                    self._write_manifest()
                except IOError as e:
                    # If manifest fails, we are in an inconsistent state, raise critical error
                    raise IOError(f"CRITICAL: Failed to write MANIFEST after L{level_idx} compaction. Old files were not deleted. {e}")

            # 4. Delete old physical files (Cleanup) (File deletion can be outside the lock)
            for sstable_id in all_input_sstables_for_compaction: 
                self.sstable_manager.delete_sstable_files(sstable_id)

            # Re-check compaction immediately after completion, as this may trigger the next level's compaction
            self._check_and_trigger_compaction()
        else:
            raise IOError(f"CRITICAL: SSTable merge failed during L{level_idx} compaction.")


    def _commit_sstables_and_meta(self, new_sstable_ids: List[str], row_count: int, csv_metadata: Dict[str, Any]) -> None:
        """
        Atomically adds newly created SSTables to L0, updates MANIFEST,
        and updates the collection's core metadata with key count and CSV schema.
        """
    
        #update levels and manifest
        with self._level_lock:
            if not self.levels:
                self.levels.append([])
            
            # Add ALL new IDs to L0
            self.levels[0].extend(new_sstable_ids) 
            
            try:
                self._write_manifest()
            except IOError as e:
                # Rollback: Remove all IDs from in-memory state if commit fails
                for sstable_id in new_sstable_ids:
                    if sstable_id in self.levels[0]:
                        self.levels[0].remove(sstable_id)
                # CRITICAL: Delete the physical files to prevent orphaned data
                for sstable_id in new_sstable_ids:
                    self.sstable_manager.delete_sstable_files(sstable_id) 
                raise IOError(f"CRITICAL: Failed to write MANIFEST after bulk SSTable creation. Import aborted. {e}")

        #update collection metadata with kv count and csv_schema
        with self._write_lock:
            self._key_count += row_count
            
            meta_file_path = self._get_meta_file_path()
            try:
                with open(meta_file_path, 'r', encoding='utf-8') as f:
                    meta_data = json.load(f)
                
                meta_data["kv_pair_count"] = self._key_count
                meta_data["csv_schema"] = csv_metadata 
                
                with open(meta_file_path, 'w', encoding='utf-8') as f:
                    json.dump(meta_data, f, indent=2)
                    
            except Exception as e:
                print(f"Warning: Failed to update metadata file {meta_file_path} after successful import. Key count may be stale. {e}")


    def import_csv(
    self, 
    csv_file_path: str, 
    key_columns: List[str], 
    value_columns: List[str], 
    csv_delimiter: str = ",",
    key_separator: str = "\x00",
    chunk_size: int = 100000, 
    max_workers: int = 8
) -> int:
        """Bulk imports a CSV file in chunks using a thread pool for concurrent SSTable writes."""
        
        if not os.path.exists(csv_file_path):
            raise FileNotFoundError(f"CSV file not found at path: {csv_file_path}")

        futures = {} # Maps Future object -> sstable_id
        total_rows_imported = 0
        
        try:
            #Concurrent Thread Pool
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                with open(csv_file_path, 'r', newline='', encoding='utf-8') as csvfile:
                    reader = csv.DictReader(csvfile, delimiter=csv_delimiter)
                    
                    #Data transformation step
                    required_cols = set(key_columns) | set(value_columns)
                    missing_cols = required_cols - set(reader.fieldnames)
                    if missing_cols:
                        raise ValueError(f"CSV header missing required columns: {missing_cols}")
                    
                    current_chunk = []
                    
                    #Create chunks by going through each row
                    for row in reader:
                        #Transform Row (Key/MsgPack Value generation)
                        key_parts = [row.get(col, '') for col in key_columns] # Use .get for robustness
                        key_string = key_separator.join(key_parts)
                        value_dict = {col: row.get(col, '') for col in value_columns}
                        value_binary = msgpack.packb(value_dict)
                        current_chunk.append((key_string, value_binary))

                        if len(current_chunk) >= chunk_size:
                            
                            # CRITICAL: Sort the chunk on the main thread (CPU-bound)
                            current_chunk.sort(key=lambda x: x[0]) 
                            
                            # Generate ID on the main thread and submit
                            sstable_id = self._generate_sstable_id()
                            future = executor.submit(self._process_and_write_chunk, current_chunk, sstable_id)
                            futures[future] = sstable_id # Track ID for rollback
                            
                            total_rows_imported += len(current_chunk)
                            current_chunk = []
                            
                    #final chunk handling
                    if current_chunk:
                        current_chunk.sort(key=lambda x: x[0])
                        sstable_id = self._generate_sstable_id()
                        future = executor.submit(self._process_and_write_chunk, current_chunk, sstable_id)
                        futures[future] = sstable_id
                        total_rows_imported += len(current_chunk)
                        
                
                #Barrier for threadpool
                new_sstable_ids = []
                
                for future in concurrent.futures.as_completed(futures):
                    sstable_id = futures[future]
                    try:
                        future.result() 
                        new_sstable_ids.append(sstable_id)
                    except Exception as e:
                        # Failure in one thread signals the failure of the entire import
                        raise RuntimeError(f"Concurrent SSTable write failed for ID {sstable_id}: {e}")

        except Exception as e:
            # --- 5. CRITICAL FAILURE HANDLING (Rollback) ---
            
            # Identify files that successfully completed their writes before the failure
            successfully_written_ids = [s_id for f, s_id in futures.items() if f.done() and not f.exception()]
            
            # Delete those successful files to maintain atomicity (0% or 100% commit)
            for sstable_id in successfully_written_ids:
                self.sstable_manager.delete_sstable_files(sstable_id)
                
            raise RuntimeError(f"Bulk CSV import failed. Rolled back {len(successfully_written_ids)} SSTables. Root error: {e}")
            
        #metadata update
        if new_sstable_ids:
            csv_meta = {
                "key_columns": key_columns,
                "value_columns": value_columns,
                "key_separator": key_separator
            }
            self._commit_sstables_and_meta(new_sstable_ids, total_rows_imported, csv_meta)
            
            # Trigger compaction check
            if self._compaction_thread and self._compaction_thread.is_alive():
                self._compaction_queue.put(("FLUSH_COMPLETE", 0))

        return total_rows_imported

    def close(self) -> None:
        self.update_metadata()
        with self._write_lock:
            if self.memtable and len(self.memtable) > 0:
                self._flush_memtable() # Ensure outstanding memtable data is flushed
            
        # NEW: Stop compaction thread gracefully
        if self._compaction_thread:
            self._compaction_stop_event.set()
            # Give the worker a chance to finish its current task or time out
            self._compaction_thread.join(timeout=5) 
        
        if self.wal:
            self.wal.close()