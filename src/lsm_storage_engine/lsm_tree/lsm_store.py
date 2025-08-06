import os
import time
import json
import shutil 

from ..abstract_kv_store import AbstractKVStore
from .wal import WriteAheadLog, TOMBSTONE 
from .memtable import Memtable 
from .sstable import SSTableManager, TOMBSTONE_VALUE 

class LSMTreeStore(AbstractKVStore):
    MANIFEST_FILE = "MANIFEST"
    WAL_FILE = "wal.log"
    SSTABLES_SUBDIR = "sstables" 
    # Default Compaction Triggers
    DEFAULT_MEMTABLE_THRESHOLD_BYTES = 4 * 1024 * 1024 # 4MB
    DEFAULT_MAX_L0_SSTABLES = 4 # Trigger L0->L1 compaction

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

        # Apply options
        current_options = options if options is not None else {}
        self.memtable_flush_threshold_bytes = current_options.get(
            "memtable_threshold_bytes", self.DEFAULT_MEMTABLE_THRESHOLD_BYTES
        )
        self.max_l0_sstables_before_compaction = current_options.get(
            "max_l0_sstables", self.DEFAULT_MAX_L0_SSTABLES
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
        sorted_items = self.memtable.get_sorted_items() # Returns list of (key, value_or_TOMBSTONE_objet)

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
            self._check_and_trigger_compaction()
        else:
            print(f"CRITICAL: Failed to write SSTable {sstable_id} during memtable flush. Data remains in memtable/WAL.")
            # Do not clear memtable or truncate WAL if SSTable write fails.


    def _check_and_trigger_compaction(self):
        if not self.levels or not self.levels[0]: # No L0 SSTables
            return

        # Simple L0 to L1 compaction: if L0 has too many SSTables
        if len(self.levels[0]) >= self.max_l0_sstables_before_compaction:
            print(f"LSMTreeStore: L0 has {len(self.levels[0])} SSTables (threshold: {self.max_l0_sstables_before_compaction}). Triggering L0->L1 compaction.")
            self._compact_level(0) # Compact level 0


    def _compact_level(self, level_idx: int):
        """
        Compacts SSTables within a given level or from this level to the next.
        compacts all SSTables in level_idx to level_idx+1.
        """
        if level_idx < 0 or level_idx >= len(self.levels) or not self.levels[level_idx]:
            return

        sstables_to_compact = list(self.levels[level_idx]) # Make a copy
        
        # Determine output SSTable ID and target level
        # create one new SSTable in the next level.
        output_sstable_id = self.sstable_manager._get_sstable_paths(self._generate_sstable_id() + f"_L{level_idx + 1}")[0].split(os.sep)[-1].replace('.dat','')

        # just merge sstables_to_compact.
        all_input_sstables_for_compaction = sstables_to_compact
        
        if self.sstable_manager.compact_sstables(all_input_sstables_for_compaction, output_sstable_id):
            # Update manifest:
            self.levels[level_idx] = [] # Clear old SSTables from L0
            target_level_idx = level_idx + 1
            if target_level_idx >= len(self.levels): # Ensure target level list exists
                self.levels.extend([[] for _ in range(target_level_idx - len(self.levels) + 1)])
            
            # Add the new SSTable to the target level.
            self.levels[target_level_idx].append(output_sstable_id)
            
            try:
                self._write_manifest()
            except IOError as e:
                raise IOError(f"CRITICAL: Failed to write MANIFEST after L{level_idx} compaction. Old files were not deleted. {e}")

            # Delete old SSTables that were successfully compacted
            for sstable_id in all_input_sstables_for_compaction: # Only delete those that were input to this specific compaction
                self.sstable_manager.delete_sstable_files(sstable_id)
        
    def close(self) -> None:
        if self.memtable and len(self.memtable) > 0:
            self._flush_memtable() # Ensure outstanding memtable data is flushed
        
        if self.wal:
            self.wal.close()
        
