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

from ..abstract_kv_store import AbstractKVStore
from .wal import WriteAheadLog, TOMBSTONE 
from .memtable import Memtable 
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
        self.memtable: Memtable | None = None
        self.sstable_manager: SSTableManager = SSTableManager(self.sstables_storage_dir)
        
        self.levels: list[list[str]] = [] 
        self._level_lock = threading.Lock() # CRITICAL: Threading Lock for `self.levels` and `MANIFEST` file access

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

    def _process_and_write_chunk(self, chunk_data: List[Tuple[str, bytes]]) -> str:
        """Helper to sort a chunk and write it to a single SSTable."""
        chunk_data.sort(key=lambda x: x[0]) 

        if not chunk_data:
            raise ValueError("Attempted to process an empty chunk.")
        sstable_id = self._generate_sstable_id()
        
        if not self.sstable_manager.write_sstable(sstable_id, chunk_data):
            raise IOError(f"Failed to write SSTable for chunk {sstable_id}.")

        return sstable_id
    def _get_meta_file_path(self) -> str: 
        """Gets the path to the collection's metadata file."""
        return os.path.join(self.collection_path, self.ENGINE_META_FILE)
    
    @property
    def key_count(self) -> int:
        return self._key_count

    def update_metadata(self) -> None:
        """Implements abstract method: reads, updates, and writes the key count."""
        meta_file_path = self._get_meta_file_path()
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
        self.memtable = Memtable(threshold_bytes=self.memtable_flush_threshold_bytes)
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
        exists_before_put = self.exists(key)
        self.wal.log_operation("PUT", key, value)
        self.memtable.put(key, value)
        if not exists_before_put and value is not TOMBSTONE: 
            self._key_count += 1
        if self.memtable.is_full():
            self._flush_memtable()
            self.update_metadata()

    def delete(self, key: str) -> None:
        if self.wal is None or self.memtable is None:
            raise RuntimeError("LSMTreeStore not properly loaded. Call load() via StorageManager.")

        exists_before_delete = self.exists(key) is not None
        self.wal.log_operation("DELETE", key)
        self.memtable.delete(key) # Uses TOMBSTONE internally
        if exists_before_delete:
            self._key_count = max(0, self._key_count - 1)
        if self.memtable.is_full(): # Or other criteria for flushing after deletes
            self._flush_memtable()
            self.update_metadata()


    def get(self, key: str) -> str | None:
        if self.memtable is None: # Check memtable existence as a proxy for loaded state
             raise RuntimeError("LSMTreeStore not properly loaded. Call load() via StorageManager.")

        mem_value = self.memtable.get(key)
        if mem_value is not None:
            return None if mem_value is TOMBSTONE else mem_value # Ensure TOMBSTONE object is used

        # Search SSTables: L0 (newest to oldest), then L1, L2...
        for level_idx, sstable_ids_in_level in enumerate(self.levels):
            search_order = reversed(sstable_ids_in_level) if level_idx == 0 else sstable_ids_in_level
            
            for sstable_id in search_order:
                if not self.sstable_manager.check_bloom_filter(sstable_id, key):
                    continue 
                # find_in_sstable returns (value, is_tombstone_found)
                # value could be TOMBSTONE_VALUE (string) if is_tombstone_found is true
                sstable_val, was_tombstone_str = self.sstable_manager.find_in_sstable(sstable_id, key)
                
                if sstable_val is not None: # Found an entry for the key
                    if was_tombstone_str or sstable_val == TOMBSTONE_VALUE:
                        return None # Key is deleted
                    return sstable_val # Return the actual value
        return None

    def _memtable_range_iterator(self, start_key: str, end_key: str) -> Iterator[Tuple[str, str]]:
        """
        Generates a range iterator for the Memtable [start_key, end_key].
        """
        if self.memtable is None:
            return
            
        # Find the starting index for the range in the sorted dict keys
        start_idx = self.memtable._data.bisect_left(start_key) 
        
        for i in range(start_idx, len(self.memtable._data)):
            key = self.memtable._data.iloc[i]
            if key >= end_key:
                break
            value = self.memtable._data[key]
            # Ensure internal TOMBSTONE object is yielded correctly as TOMBSTONE_VALUE string
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

        # --- 1. Sources: Memtable (Source ID 0 - Newest) ---
        # The value is the iterator object itself
        memtable_it = self._memtable_range_iterator(start_key, end_key)
        try:
            key, value = next(memtable_it)
            # Heap entry structure: (key, source_id, value, iterator)
            heapq.heappush(heap, (key, source_id_counter, value, memtable_it))
        except StopIteration:
            pass # Memtable is empty in this range
        source_id_counter += 1

        # --- 2. Sources: SSTables (Levels L0, L1, L2... L0 gets lower IDs) ---
        
        with self._level_lock:
            for level_idx, sstable_ids_in_level in enumerate(self.levels):
                # The source_id counter ensures that items from lower (newer) levels 
                # are prioritized when keys are equal in the heap sort order.
                for sstable_id in sstable_ids_in_level:
                    # Optimization: Use metadata to check for key range overlap before opening file
                    range_info = self.sstable_manager.get_sstable_key_range(sstable_id)
                    if range_info:
                        meta_min, meta_max = range_info
                        # Skip if requested range [start_key, end_key) does NOT overlap with SSTable [meta_min, meta_max]
                        # Overlap occurs if (start_key < meta_max) AND (end_key > meta_min)
                        if start_key > meta_max or end_key <= meta_min:
                             continue
                    
                    # Create the iterator for this SSTable
                    sstable_it = self.sstable_manager.range_iterator(sstable_id, start_key, end_key)
                    
                    try:
                        key, value = next(sstable_it)
                        # Push to heap. The `source_id_counter` is the version differentiator.
                        heapq.heappush(heap, (key, source_id_counter, value, sstable_it))
                        source_id_counter += 1
                    except StopIteration:
                        # SSTable is empty in this range
                        continue

        # --- 3. The Merge Loop (Version Resolution) ---
        last_key = None
        latest_value = None
        
        while heap:
            # key, source_id, value, iterator
            key, _, value, iterator = heapq.heappop(heap)

            # If this is the first key in the whole process, or if the key is new:
            if key != last_key:
                # If we finished processing the last key, yield the resolved value for it
                if last_key is not None:
                    # Yield the result for the last_key only if it wasn't a tombstone
                    if latest_value != TOMBSTONE_VALUE and latest_value is not None:
                        yield (last_key, latest_value)
                
                # Start tracking the new key
                last_key = key
                latest_value = value
            # If key == last_key, we found an older version (because of the heap order). 
            # We already have the newest version (the first one seen for this key), 
            # so we simply ignore this entry and continue.

            # Refill the heap from the iterator we just used
            try:
                next_key, next_value = next(iterator)
                heapq.heappush(heap, (next_key, source_id_counter, next_value, iterator))
            except StopIteration:
                pass # This source is exhausted

        # --- 4. Final Key Yield ---
        # Yield the very last key processed
        if last_key is not None and latest_value != TOMBSTONE_VALUE and latest_value is not None:
             yield (last_key, latest_value)

    def exists(self, key: str) -> bool:
        return self.get(key) is not None


    def _flush_memtable(self) -> None:
        if not self.memtable or not self.wal or len(self.memtable) == 0:
            return

        sstable_id = self._generate_sstable_id()
        sorted_items = sorted_items = [(k, v if v is not TOMBSTONE else TOMBSTONE_VALUE) 
                        for k, v in self.memtable.get_sorted_items()]
        
        if self.sstable_manager.write_sstable(sstable_id, sorted_items):
            
            with self._level_lock: # Protect shared state modification
                # Add to L0 (Level 0). L0 is self.levels[0]
                if not self.levels: # First level (L0) doesn't exist
                    self.levels.append([])
                self.levels[0].append(sstable_id) # Append to L0, newest L0 sstables are at the end
                
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
            raise IOError(f"CRITICAL: Failed to write SSTable {sstable_id} during memtable flush. Data remains in memtable/WAL.")

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


    def _commit_sstables_and_meta(self, new_sstable_id: str, row_count: int, csv_metadata: Dict[str, Any]) -> None:
        """
        Atomically adds a newly created SSTable to L0, updates MANIFEST,
        and updates the collection's core metadata with key count and CSV schema.
        """
        
        #update levels and manifest
        with self._level_lock:
            if not self.levels:
                self.levels.append([])
            
            # Add to L0. The newest L0 sstables are at the end.
            self.levels[0].append(new_sstable_id) 
            
            try:
                self._write_manifest()
            except IOError as e:
                # Rollback: Remove ID from in-memory state if commit fails
                self.levels[0].remove(new_sstable_id)
                # CRITICAL: Delete the physical files to prevent orphaned data
                self.sstable_manager.delete_sstable_files(new_sstable_id) 
                raise IOError(f"CRITICAL: Failed to write MANIFEST after bulk SSTable creation. Import aborted. {e}")

        # 2. Update Collection Metadata (Including new CSV Schema)
        self._key_count += row_count
        
        meta_file_path = self._get_meta_file_path()
        try:
            with open(meta_file_path, 'r', encoding='utf-8') as f:
                meta_data = json.load(f)
            #update the kv pair count
            meta_data["kv_pair_count"] = self._key_count
            # Store the new, required CSV schema for export
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
    chunk_size: int = 100000 # Default to 100,000 rows as a memory heuristic
) -> int:
        
        """Bulk imports a CSV file in chunks, creating multiple L0 SSTables if necessary."""
    
        if not os.path.exists(csv_file_path):
            raise FileNotFoundError(f"CSV file not found at path: {csv_file_path}")

        new_sstable_ids = []
        total_rows_imported = 0
        
        # The try/except block encompasses the entire file reading and writing process 
        # to ensure atomic rollback if any chunk fails.
        try:
            with open(csv_file_path, 'r', newline='', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile, delimiter=csv_delimiter)
                
                #column validation
                required_cols = set(key_columns) | set(value_columns)
                missing_cols = required_cols - set(reader.fieldnames)
                if missing_cols:
                    raise ValueError(f"CSV header missing required columns: {missing_cols}")
                
                current_chunk = []
                
                #go through each row for in memory chunking in case of large csv file
                for row in reader:
                    #Row formatting into kv piar
                    key_parts = [row[col] for col in key_columns]
                    key_string = key_separator.join(key_parts)
                    value_dict = {col: row[col] for col in value_columns}
                    value_binary = msgpack.packb(value_dict)
                    current_chunk.append((key_string, value_binary))
                    
                    #check chunk size and flush if greater
                    if len(current_chunk) >= chunk_size:
                        # Write the chunk and track the new SSTable ID
                        sstable_id = self._process_and_write_chunk(current_chunk)
                        new_sstable_ids.append(sstable_id)
                        total_rows_imported += len(current_chunk)
                        
                        # Reset the chunk for the next batch
                        current_chunk = []
                        
                # final chunk processing
                if current_chunk:
                    sstable_id = self._process_and_write_chunk(current_chunk)
                    new_sstable_ids.append(sstable_id)
                    total_rows_imported += len(current_chunk)

        except Exception as e:
            # CRITICAL FAILURE HANDLING (Rollback)
            # Delete any successfully written SSTables from this *attempt*
            for sstable_id in new_sstable_ids:
                self.sstable_manager.delete_sstable_files(sstable_id)
            raise RuntimeError(f"Bulk CSV import failed during processing. Rolled back {len(new_sstable_ids)} SSTables. Error: {e}")
            
        #Commiting to manifest with metadata, useful for exporting
        if new_sstable_ids:
            csv_meta = {
                "key_columns": key_columns,
                "value_columns": value_columns,
                "key_separator": key_separator
            }
            # Commit all new SSTable IDs and update collection metadata atomically
            self._commit_sstables_and_meta(new_sstable_ids, total_rows_imported, csv_meta)
            
            # Trigger compaction check as multiple new L0 files may exceed the threshold (default 4)
            if self._compaction_thread and self._compaction_thread.is_alive():
                self._compaction_queue.put(("FLUSH_COMPLETE", 0))

        return total_rows_imported

    def close(self) -> None:
        self.update_metadata()
        if self.memtable and len(self.memtable) > 0:
            self._flush_memtable() # Ensure outstanding memtable data is flushed
        
        # NEW: Stop compaction thread gracefully
        if self._compaction_thread:
            self._compaction_stop_event.set()
            # Give the worker a chance to finish its current task or time out
            self._compaction_thread.join(timeout=5) 
        
        if self.wal:
            self.wal.close()