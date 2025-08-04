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
        if os.path.exists(self.manifest_path):
            try:
                with open(self.manifest_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    loaded_levels = data.get("levels", [])
                    # Basic validation (e.g., ensure it's a list of lists of strings)
                    if isinstance(loaded_levels, list) and \
                       all(isinstance(level, list) for level in loaded_levels) and \
                       all(isinstance(sid, str) for level in loaded_levels for sid in level):
                        self.levels = loaded_levels
                        print(f"LSMTreeStore: MANIFEST loaded. Levels: {len(self.levels)}")
                        return True
                    else:
                        print(f"Error: MANIFEST file {self.manifest_path} has invalid format. Re-initializing.")
                        self.levels = [] # Corrupted, treat as new
            except (IOError, json.JSONDecodeError) as e:
                print(f"Error reading or parsing MANIFEST {self.manifest_path}: {e}. Re-initializing store state.")
                self.levels = []
                return False # Indicate manifest load failure
        else:
            print("LSMTreeStore: MANIFEST file not found. Initializing new store state.")
            self.levels = [] # No manifest, start fresh (new collection)
        return True


    def load(self) -> None:
        print(f"LSMTreeStore: Loading data for {self.collection_path}")
        
        if not self._load_manifest():
            print(f"DEBUG: LSMTreeStore.load() - MANIFEST load failed or indicated re-init.")
            print(f"LSMTreeStore: CRITICAL - Failed to load or initialize MANIFEST. Store may be in an inconsistent state.")
            
        try:
            self.wal = WriteAheadLog(self.wal_path)
            print(f"DEBUG: LSMTreeStore.load() - WAL initialized: {self.wal is not None}")
        except Exception as e: # Catch errors from WAL initialization (e.g. file permission)
            print(f"LSMTreeStore: CRITICAL - Failed to initialize WAL: {e}. Store cannot operate.")
            raise # Re-raise to signal failure to StorageManager

        self.memtable = Memtable(threshold_bytes=self.memtable_flush_threshold_bytes)
        print(f"DEBUG: LSMTreeStore.load() - Memtable initialized: {self.memtable is not None}")
        wal_entries = self.wal.replay() # WAL replay should handle its own errors gracefully
        print("the wal entries are:",wal_entries)
        if wal_entries:
            print(f"LSMTreeStore: Replaying {len(wal_entries)} WAL entries...")
            for entry in wal_entries:
                op_type = entry.get("op")
                key = entry.get("key")
                if op_type == "PUT":
                    value = entry.get("value")
                    self.memtable.put(key, value)
                elif op_type == "DELETE":
                    self.memtable.delete(key) # Internally uses TOMBSTONE
            print(f"LSMTreeStore: WAL replay complete. Memtable: {len(self.memtable)} entries, ~{self.memtable.estimated_size()} bytes.")
        
        if self.wal is not None and self.memtable is not None:
            print(f"DEBUG: LSMTreeStore.load() FINISHED SUCCESSFULLY for {self.collection_path}. Instance ID: {id(self)}. self.wal is None: {self.wal is None}, self.memtable is None: {self.memtable is None}")
        else:
            print(f"DEBUG: LSMTreeStore.load() FINISHED WITH ISSUES for {self.collection_path}. Instance ID: {id(self)}. self.wal is None: {self.wal is None}, self.memtable is None: {self.memtable is None}")

    def put(self, key: str, value: str) -> None:
        print(f"DEBUG: LSMTreeStore.put() ENTERED. Instance ID: {id(self)}. self.wal is None: {self.wal is None}. self.memtable is None: {self.memtable is None}")
        if self.wal is None or self.memtable is None:
            error_msg = "LSMTreeStore not properly loaded. "
            if not self.wal: error_msg += "WAL is None. "
            if not self.memtable: error_msg += "Memtable is None. "
            error_msg += "Call load() via StorageManager."
            raise Exception(error_msg)
        if not self.wal.log_operation("PUT", key, value):
            print(f"CRITICAL ERROR: Failed to write PUT to WAL for key '{key}'. Operation aborted.")
            # In a real DB, this might trigger a read-only mode or other safety measures.
            return 

        self.memtable.put(key, value)

        if self.memtable.is_full():
            print(f"LSMTreeStore: Memtable reached threshold ({self.memtable.estimated_size()} bytes). Flushing.")
            self._flush_memtable()


    def delete(self, key: str) -> None:
        if self.wal is None or self.memtable is None:
            raise Exception("LSMTreeStore not properly loaded. Call load() via StorageManager.")

        if not self.wal.log_operation("DELETE", key):
            print(f"CRITICAL ERROR: Failed to write DELETE to WAL for key '{key}'. Operation aborted.")
            return

        self.memtable.delete(key) # Uses TOMBSTONE internally

        if self.memtable.is_full(): # Or other criteria for flushing after deletes
            print(f"LSMTreeStore: Memtable reached threshold after delete. Flushing.")
            self._flush_memtable()


    def get(self, key: str) -> str | None:
        if self.memtable is None: # Check memtable existence as a proxy for loaded state
             raise Exception("LSMTreeStore not properly loaded. Call load() via StorageManager.")

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
        val = self.get(key) # Leverage get logic
        return val is not None


    def _flush_memtable(self) -> None:
        if not self.memtable or not self.wal:
            print("Error: _flush_memtable called but store not fully initialized.")
            return
        if len(self.memtable) == 0:
            print("LSMTreeStore: Memtable is empty, no flush needed.")
            return

        sstable_id = self._generate_sstable_id()
        print(f"LSMTreeStore: Flushing memtable ({len(self.memtable)} entries) to SSTable ID: {sstable_id}")
        sorted_items = self.memtable.get_sorted_items() # Returns list of (key, value_or_TOMBSTONE_objet)

        if self.sstable_manager.write_sstable(sstable_id, sorted_items):
            # Add to L0 (Level 0). L0 is self.levels[0]
            if not self.levels: # First level (L0) doesn't exist
                self.levels.append([])
            self.levels[0].append(sstable_id) # Append to L0, newest L0 sstables are at the end
            
            if not self._write_manifest():
                print(f"CRITICAL: Failed to write MANIFEST after flushing {sstable_id}. Attempting to revert.")
                if sstable_id in self.levels[0]: self.levels[0].remove(sstable_id)
                self.sstable_manager.delete_sstable_files(sstable_id)
                # Do NOT truncate WAL or clear memtable if manifest fails.
                return 

            self.memtable.clear()
            self.wal.truncate()
            print(f"LSMTreeStore: Flush to {sstable_id} successful. Memtable cleared, WAL truncated.")

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
            print(f"LSMTreeStore: Nothing to compact in L{level_idx} or level does not exist.")
            return

        sstables_to_compact = list(self.levels[level_idx]) # Make a copy
        
        # Determine output SSTable ID and target level
        # create one new SSTable in the next level.
        output_sstable_id = self.sstable_manager._get_sstable_paths(self._generate_sstable_id() + f"_L{level_idx + 1}")[0].split(os.sep)[-1].replace('.dat','')


        print(f"LSMTreeStore: Compacting L{level_idx} SSTables: {sstables_to_compact} -> {output_sstable_id} (L{level_idx+1})")

        # just merge sstables_to_compact.
        all_input_sstables_for_compaction = sstables_to_compact
        
        if self.sstable_manager.compact_sstables(all_input_sstables_for_compaction, output_sstable_id):
            print(f"LSMTreeStore: Compaction of L{level_idx} successful. New SSTable: {output_sstable_id}")
            
            # Update manifest:
            self.levels[level_idx] = [] # Clear old SSTables from L0
            target_level_idx = level_idx + 1
            if target_level_idx >= len(self.levels): # Ensure target level list exists
                self.levels.extend([[] for _ in range(target_level_idx - len(self.levels) + 1)])
            
            # Add the new SSTable to the target level.
            self.levels[target_level_idx].append(output_sstable_id)
            
            if not self._write_manifest():
                print(f"CRITICAL: Failed to write MANIFEST after L{level_idx} compaction. State may be inconsistent.")
                # For now, we don't attempt a complex rollback of self.levels.
                return # Don't delete old files if manifest fails

            # Delete old SSTables that were successfully compacted
            for sstable_id in all_input_sstables_for_compaction: # Only delete those that were input to this specific compaction
                self.sstable_manager.delete_sstable_files(sstable_id)
            print(f"LSMTreeStore: Old L{level_idx} SSTables deleted: {all_input_sstables_for_compaction}")
        else:
            print(f"LSMTreeStore: Compaction of L{level_idx} failed for output {output_sstable_id}. Old SSTables remain.")


    def close(self) -> None:
        print(f"LSMTreeStore: Closing store for {self.collection_path}...")
        if self.memtable and len(self.memtable) > 0:
            print("LSMTreeStore: Memtable not empty on close, attempting final flush.")
            self._flush_memtable() # Ensure outstanding memtable data is flushed
        
        if self.wal:
            self.wal.close()
        
        print(f"LSMTreeStore: Store for {self.collection_path} closed.")

