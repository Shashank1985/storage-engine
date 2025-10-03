import os
import time
import json
import shutil 
import threading
import queue

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
        
        # load() will be called by StorageManager after instantiation.
        # If it were called here, and load() failed, the object might be in an inconsistent state.

    def _generate_sstable_id(self) -> str:
        return f"sst_{int(time.time() * 1000000)}_{len(self.sstable_manager.get_all_sstable_ids_from_disk())}"


    def _write_manifest(self) -> bool:
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
        self.wal.log_operation("PUT", key, value)
        self.memtable.put(key, value)
        if self.memtable.is_full():
            self._flush_memtable()

    def delete(self, key: str) -> None:
        if self.wal is None or self.memtable is None:
            raise RuntimeError("LSMTreeStore not properly loaded. Call load() via StorageManager.")

        self.wal.log_operation("DELETE", key)
        self.memtable.delete(key) # Uses TOMBSTONE internally

        if self.memtable.is_full(): # Or other criteria for flushing after deletes
            self._flush_memtable()


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
                # find_in_sstable returns (value, is_tombstone_found)
                # value could be TOMBSTONE_VALUE (string) if is_tombstone_found is true
                sstable_val, was_tombstone_str = self.sstable_manager.find_in_sstable(sstable_id, key)
                
                if sstable_val is not None: # Found an entry for the key
                    if was_tombstone_str or sstable_val == TOMBSTONE_VALUE:
                        return None # Key is deleted
                    return sstable_val # Return the actual value
        return None


    def exists(self, key: str) -> bool:
        return self.get(key) is not None


    def _flush_memtable(self) -> None:
        if not self.memtable or not self.wal or len(self.memtable) == 0:
            return

        sstable_id = self._generate_sstable_id()
        sorted_items = sorted_items = [(k, v if v is not TOMBSTONE else TOMBSTONE_VALUE) 
                        for k, v in self.memtable.get_sorted_items()]
        
        if self.sstable_manager.write_sstable(sstable_id, sorted_items):
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
                pass 
            finally:
                self._compaction_queue.task_done()

    def _check_and_trigger_compaction(self):
        """
        Checks for and triggers the next required compaction task.
        Called by the background compaction thread.
        """
        if not self.levels or not self.levels[0]: # No L0 SSTables
            return

        # 1. Simple L0 to L1 compaction (Tiered Compaction)
        # L0 is tiered and merges ALL into L1 when capacity is reached.
        if len(self.levels[0]) >= self.max_l0_sstables_before_compaction:
            self._compact_level(0) # Compact level 0
            return # Run one major compaction at a time

        # 2. L1+ to L(i+1) compaction (Leveled/Partial Compaction)
        # Check all levels L1 and above for over-capacity (using file count proxy)
        for level_idx in range(1, len(self.levels)):
            if len(self.levels[level_idx]) > self.LEVELED_CAPACITY_PROXY:
                self._compact_level(level_idx)
                return # Run one major compaction and exit


    def _compact_level(self, level_idx: int):
        """
        Compacts SSTables within a given level or from this level to the next.
        Uses Tiered compaction for L0->L1 and Leveled compaction for L1+->L2+.
        """
        if level_idx < 0 or level_idx >= len(self.levels) or not self.levels[level_idx]:
            return

        # Ensure target level exists
        target_level_idx = level_idx + 1
        if target_level_idx >= len(self.levels): # Ensure target level list exists
            self.levels.extend([[] for _ in range(target_level_idx - len(self.levels) + 1)])
        
        all_input_sstables_for_compaction: list[str] = []
        output_sstable_id = self._generate_sstable_id() 
        
        # --- L0 -> L1 Compaction (Tiered/Full Merge) ---
        if level_idx == 0:
            all_input_sstables_for_compaction = list(self.levels[0])

        # --- L1+ -> L2+ Compaction (Leveled/Partial Merge) ---
        else: 
            
            # 1. Select the oldest/smallest SSTable from the source level (L(i))
            sstable_to_compact = self.levels[level_idx][0] # Always choose the oldest/first file
            
            range_info = self.sstable_manager.get_sstable_key_range(sstable_to_compact)
            if not range_info:
                raise LSMCompactionError(f"CRITICAL: Failed to get key range for L{level_idx} sstable {sstable_to_compact}. Compaction aborted.")
            
            source_min_key, source_max_key = range_info

            # Collect files for merging
            sstables_to_merge = [sstable_to_compact]
            target_sstables_to_remove = []
            
            # 2. Find ALL overlapping SSTables in the target level (L(i+1))
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

        # Perform the actual k-way merge
        if self.sstable_manager.compact_sstables(all_input_sstables_for_compaction, output_sstable_id):
            
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

            # 4. Delete old physical files (Cleanup)
            for sstable_id in all_input_sstables_for_compaction: 
                self.sstable_manager.delete_sstable_files(sstable_id)

            # Re-check compaction immediately after completion, as this may trigger the next level's compaction
            self._check_and_trigger_compaction()
        else:
            raise IOError(f"CRITICAL: SSTable merge failed during L{level_idx} compaction.")

            
    def close(self) -> None:
        if self.memtable and len(self.memtable) > 0:
            self._flush_memtable() # Ensure outstanding memtable data is flushed
        
        # NEW: Stop compaction thread gracefully
        if self._compaction_thread:
            self._compaction_stop_event.set()
            # Give the worker a chance to finish its current task or time out
            self._compaction_thread.join(timeout=5) 
        
        if self.wal:
            self.wal.close()