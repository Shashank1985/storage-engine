"""Simple SSTable implementation with sparse index storing key and file offset and tombstone handling.
This module provides functionality to write, read, and compact SSTables.
Merges and compacts multiple SSTables into a single one using a k-way merge algorithm (similar to merge k sorted lists Leetcode).
everytime a new SSTable is created, it is written to disk and sparse index is created.
"""
import os
import json
import bisect
import heapq 

TOMBSTONE_VALUE = "__TOMBSTONE__" 

class SSTableManager:
    """
    Manages reading from and writing to SSTable files.
    Also handles sparse indexes.
    """
    # For a very simple sparse index: store every Nth key and its offset
    SPARSE_INDEX_SAMPLING_RATE = 10 # Store one index entry for every 10 data entries

    def __init__(self, sstables_dir: str):
        self.sstables_dir = sstables_dir
        os.makedirs(self.sstables_dir, exist_ok=True)

    def _get_sstable_paths(self, sstable_id: str) -> tuple[str, str]:
        """Helper to get data and index file paths."""
        base_path = os.path.join(self.sstables_dir, sstable_id)
        data_path = f"{base_path}.dat"  # Data file
        index_path = f"{base_path}.idx" # Index file
        meta_path = f"{base_path}.meta" #metadata file to hold min_key and max_key
        return data_path, index_path,meta_path

    def write_sstable(self, sstable_id: str, sorted_items: list[tuple[str, any]]) -> bool:
        """
        Writes a list of sorted key-value items to a new SSTable and its sparse index.
        `sorted_items` is a list of (key, value) tuples, where value can be TOMBSTONE_VALUE.
        """
        data_path, index_path,meta_path = self._get_sstable_paths(sstable_id)
        sparse_index_entries = []
        current_offset = 0
        entry_count = 0

        try:
            with open(data_path, 'w', encoding='utf-8') as data_f:
                for key, value in sorted_items:
                    actual_value = TOMBSTONE_VALUE if value is TOMBSTONE_VALUE else value
                    
                    log_entry = {"key": key, "value": actual_value}
                    json_line = json.dumps(log_entry) + '\n'
                    
                    if entry_count % self.SPARSE_INDEX_SAMPLING_RATE == 0: #append every 10 entries
                        sparse_index_entries.append({"key": key, "offset": current_offset})
                    
                    data_f.write(json_line)
                    current_offset += len(json_line.encode('utf-8')) # More accurate byte offset
                    entry_count += 1
            
            # Write the sparse index
            if sparse_index_entries: # Only write if there's something to index
                with open(index_path, 'w', encoding='utf-8') as index_f:
                    json.dump(sparse_index_entries, index_f)
            
            if entry_count > 0:
                min_key = sorted_items[0][0]
                max_key = sorted_items[-1][0]
                meta_data = {"min_key": min_key, "max_key": max_key}
                with open(meta_path, 'w', encoding='utf-8') as meta_f:
                    json.dump(meta_data, meta_f)
                return True
            return False
        except IOError as e:
            if os.path.exists(data_path): os.remove(data_path)
            if os.path.exists(index_path): os.remove(index_path)
            if os.path.exists(meta_path): os.remove(meta_path)
            raise IOError(f"Error writing SSTable {sstable_id}: {e}")

    def find_in_sstable(self, sstable_id: str, target_key: str) -> tuple[any, bool]:
        data_path, index_path,_ = self._get_sstable_paths(sstable_id)

        if not os.path.exists(data_path):
            return None, False

        start_offset = 0
        #use the sparse index first
        if os.path.exists(index_path):
            try:
                with open(index_path, 'r', encoding='utf-8') as index_f:
                    sparse_index_entries = json.load(index_f)
                
                if sparse_index_entries: 
                    # Extract just the keys for bisect, as bisect works on sorted lists.
                    index_keys = [entry["key"] for entry in sparse_index_entries]
                    idx = bisect.bisect_right(index_keys, target_key)
                    
                    if idx > 0:
                        start_offset = sparse_index_entries[idx-1]["offset"]
                    
            except (IOError, json.JSONDecodeError, IndexError) as e: # Added IndexError
                start_offset = 0
        try:
            with open(data_path, 'r', encoding='utf-8') as data_f:
                if start_offset > 0:
                    data_f.seek(start_offset)
                
                # For now, we scan until we find the key or a larger key.
                for line_number, line in enumerate(data_f): # enumerate for potential debug/logging
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        current_key = entry["key"]
                         

                        if current_key == target_key:
                            value = entry.get("value")
                            return value, value == TOMBSTONE_VALUE
                            
                        # Optimization: if current_key is already greater than target_key,
                        # and the SSTable is sorted, we can stop early.
                        if current_key > target_key:
                            return None, False 

                    except json.JSONDecodeError:
                        continue 
            
            return None, False # Key not found after scanning relevant part (or whole file)
        except IOError as e:
            raise IOError(f"Error reading SSTable {data_path}: {e}")
    def get_all_sstable_ids_from_disk(self) -> list[str]:
        """
        Scans the sstables_dir and returns a sorted list of SSTable IDs.
        SSTable ID is the filename without .dat or .idx.
        Assumes IDs can be naturally sorted (e.g., timestamp-based or zero-padded numbers).
        """
        ids = set()
        if not os.path.exists(self.sstables_dir):
            return []
        for filename in os.listdir(self.sstables_dir):
            if filename.endswith(".dat"):
                ids.add(filename[:-4]) # Remove .dat
        return sorted(list(ids)) #(oldest to newest)

    def get_sstable_key_range(self, sstable_id: str) -> tuple[str, str] | None:
        """Retrieves the min and max key for an SSTable from its metadata file."""
        _, _, meta_path = self._get_sstable_paths(sstable_id)
        if os.path.exists(meta_path):
            try:
                with open(meta_path, 'r', encoding='utf-8') as f:
                    meta_data = json.load(f)
                return meta_data["min_key"], meta_data["max_key"]
            except (IOError, json.JSONDecodeError, KeyError) as e:
                print(f"Warning: Could not read meta file for {sstable_id}: {e}")
                return None
        return None

    # --- Compaction Related (Basic merging k sorted lists) ---
    def compact_sstables(self, sstable_ids_to_compact: list[str], output_sstable_id: str) -> bool:
        heap = []
        iterators = []
        try:
            for i, sstable_id in enumerate(sstable_ids_to_compact):
                data_path, _,_ = self._get_sstable_paths(sstable_id)
                if not os.path.exists(data_path): continue
                
                file_handle = open(data_path, 'r', encoding='utf-8')
                it = self._sstable_line_reader(file_handle, i)
                iterators.append(it) # Keep track to close file handle
                
                try:
                    first_entry, s_idx = next(it)
                    heapq.heappush(heap, (first_entry["key"], s_idx, first_entry.get("value")))
                except StopIteration:
                    file_handle.close()
        except IOError as e:
            raise IOError(f"Error opening SSTables for compaction: {e}")

        merged_results = []
        last_key = object() # Unique sentinel
        
        while heap:
            key, sstable_idx, value = heapq.heappop(heap)
            
            if key != last_key:
                if last_key is not object(): # Process the previous key's latest value
                    if 'latest_value' in locals() and latest_value != TOMBSTONE_VALUE:
                        merged_results.append((last_key, latest_value))
                last_key = key
                latest_value = value
                latest_sstable_idx = sstable_idx
            elif sstable_idx > latest_sstable_idx: # Same key, newer sstable
                latest_value = value
                latest_sstable_idx = sstable_idx
            
            try:
                next_entry, next_sstable_idx = next(iterators[sstable_idx])
                heapq.heappush(heap, (next_entry["key"], next_sstable_idx, next_entry.get("value")))
            except StopIteration:
                continue

        if last_key is not object() and latest_value != TOMBSTONE_VALUE:
            merged_results.append((last_key, latest_value))
        
        if merged_results:
            self.write_sstable(output_sstable_id, merged_results)
        
        return True

    def _sstable_line_reader(self, file_handle, sstable_index):
        try:
            for line in file_handle:
                if line.strip():
                    try:
                        yield json.loads(line), sstable_index
                    except json.JSONDecodeError:
                        continue
        finally:
            file_handle.close()
    def delete_sstable_files(self, sstable_id: str):
        """Deletes the .dat and .idx files for a given sstable_id."""
        data_path, index_path,meta_path = self._get_sstable_paths(sstable_id)
        deleted_count = 0
        try:
            if os.path.exists(data_path):
                os.remove(data_path)
                deleted_count+=1
            if os.path.exists(index_path):
                os.remove(index_path)
                deleted_count+=1
            if os.path.exists(meta_path):
                os.remove(meta_path)
                deleted_count+=1
            
        except IOError as e:
            raise IOError(f"Error deleting SSTable files for ID {sstable_id}: {e}")