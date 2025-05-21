"""Simple SSTable implementation with sparse index storing key and file offset and tombstone handling.
This module provides functionality to write, read, and compact SSTables.
Merges and compacts multiple SSTables into a single one using a k-way merge algorithm (similar to merge k sorted lists Leetcode).
everytime a new SSTable is created, it is written to disk and sparse index is created.
"""
import os
import json
import bisect
import heapq 

TOMBSTONE_VALUE = "__TOMBSTONE__" # Unique object to represent a deleted value (tombstone)

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
        return data_path, index_path

    def write_sstable(self, sstable_id: str, sorted_items: list[tuple[str, any]]) -> bool:
        """
        Writes a list of sorted key-value items to a new SSTable and its sparse index.
        `sorted_items` is a list of (key, value) tuples, where value can be TOMBSTONE_VALUE.
        """
        data_path, index_path = self._get_sstable_paths(sstable_id)
        sparse_index_entries = []
        current_offset = 0
        entry_count = 0

        try:
            with open(data_path, 'w', encoding='utf-8') as data_f:
                for key, value in sorted_items:
                    # For JSON, ensure TOMBSTONE object is converted to its string representation
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
            
            print(f"SSTable {sstable_id} and its index written successfully.")
            return True
        except IOError as e:
            print(f"Error writing SSTable {data_path} or index {index_path}: {e}")
            # Clean up potentially partially written files
            if os.path.exists(data_path): os.remove(data_path)
            if os.path.exists(index_path): os.remove(index_path)
            return False

    def find_in_sstable(self, sstable_id: str, target_key: str) -> tuple[any, bool]:
        data_path, index_path = self._get_sstable_paths(sstable_id)

        if not os.path.exists(data_path):
            print(f"SSTable data file {data_path} not found.") 
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
                    # Use bisect to find the insertion point for target_key
                    idx = bisect.bisect_right(index_keys, target_key)
                    
                    if idx > 0:
                        # The entry at sparse_index_entries[idx-1] is the one whose key is
                        # the largest key in the index that is less than or equal to target_key.
                        start_offset = sparse_index_entries[idx-1]["offset"]
                    # If idx is 0, target_key is smaller than all keys in the index, so start_offset remains 0 (full scan from beginning).

            except (IOError, json.JSONDecodeError, IndexError) as e: # Added IndexError
                print(f"Warning: Error reading, parsing, or using index {index_path}, falling back to full scan: {e}")
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
                        value = entry.get("value") 

                        if current_key == target_key:
                            if value == TOMBSTONE_VALUE:
                                return TOMBSTONE_VALUE, True
                            return value, False
                        
                        # Optimization: if current_key is already greater than target_key,
                        # and the SSTable is sorted, we can stop early.
                        if current_key > target_key:
                            return None, False 

                    except json.JSONDecodeError:
                        print(f"Warning: Corrupt data line in {data_path} (line num in scan: {line_number+1}), skipping: '{line[:50]}...'")
                        continue 
            
            return None, False # Key not found after scanning relevant part (or whole file)
        except IOError as e:
            print(f"Error reading SSTable {data_path}: {e}")
            return None, False
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

    # --- Compaction Related (Basic merging k sorted lists) ---
    def compact_sstables(self, sstable_ids_to_compact: list[str], output_sstable_id: str) -> bool:
        """
        Merges multiple SSTables into a single new SSTable.
        Removes duplicate keys (keeping the one from the "newest" SSTable based on order in list as per DDIA)
        and fully deleted entries (where a tombstone is the latest version).
        
        `sstable_ids_to_compact` should be sorted from oldest to newest.
        """
        print(f"Starting compaction for SSTables: {sstable_ids_to_compact} into {output_sstable_id}")
        
        # Min-heap to manage iterators for k-way merge
        # Each item in heap: (current_key, current_value, sstable_index, sstable_iterator)
        heap = []
        
        # Open all SSTables and create iterators
        sstable_iterators_details = []
        try:
            for i, sstable_id in enumerate(sstable_ids_to_compact):
                data_path, _ = self._get_sstable_paths(sstable_id)
                if not os.path.exists(data_path):
                    print(f"Warning: SSTable {data_path} not found during compaction. Skipping.")
                    continue
                
                file_handle = open(data_path, 'r', encoding='utf-8')
                # Create a generator for each SSTable
                def sstable_line_reader(f, s_idx):
                    for line_content in f:
                        line_content = line_content.strip()
                        if line_content:
                            try:
                                yield json.loads(line_content), s_idx
                            except json.JSONDecodeError:
                                print(f"Warning: Corrupt line in {f.name} during compaction. Skipping.")
                                continue
                    f.close()
                
                it = sstable_line_reader(file_handle,i)
                sstable_iterators_details.append(it) 
                
                # Get the first entry and push to heap
                try:
                    first_data, s_idx = next(it)
                    heapq.heappush(heap, (first_data["key"], first_data.get("value"), s_idx, it))
                except StopIteration:
                    file_handle.close()
        except IOError as e:
            print(f"Error opening SSTables for compaction: {e}")
            # Ensure any opened files are closed if error occurs mid-setup
            for it_detail in sstable_iterators_details:
                 pass
            return False

        if not heap:
            print("No data to compact.")
            return True


        merged_results = []
        last_processed_key_for_dedup = None
        
        while heap:
            key, value, sstable_idx, current_iterator = heapq.heappop(heap)

            # Handle all occurrences of this 'key' from the heap to pick the latest one
            if last_processed_key_for_dedup != key:
                # This is the first time we see this key in this iteration
                # It's guaranteed to be the smallest key overall due to min-heap.
                # Now we need to gather all other entries for this *same key* from the heap
                # to determine the true "latest" value based on sstable_idx.
                
                all_values_for_this_key = [(value, sstable_idx)]
                
                # Peek and pop other entries for the same key
                while heap and heap[0][0] == key:
                    _, next_val, next_sstable_idx, next_iterator = heapq.heappop(heap)
                    all_values_for_this_key.append((next_val, next_sstable_idx))
                    # Push next from *that* iterator
                    try:
                        next_entry_data, s_idx_from_it = next(next_iterator)
                        heapq.heappush(heap, (next_entry_data["key"], next_entry_data.get("value"), s_idx_from_it, next_iterator))
                    except StopIteration:
                        pass # This other iterator is exhausted

                # Determine the latest value for 'key' from all_values_for_this_key
                # (Newer sstables have higher sstable_idx in original input list sstable_ids_to_compact)
                latest_value = None
                max_sstable_idx = -1
                for val_k, s_idx_k in all_values_for_this_key:
                    if s_idx_k > max_sstable_idx:
                        max_sstable_idx = s_idx_k
                        latest_value = val_k
                
                if latest_value is not None and latest_value != TOMBSTONE_VALUE:
                    merged_results.append((key, latest_value))
                
                last_processed_key_for_dedup = key

            # Push the next item from the current_iterator (that yielded the initial key for this round)
            try:
                next_entry_data, s_idx_from_it = next(current_iterator)
                heapq.heappush(heap, (next_entry_data["key"], next_entry_data.get("value"), s_idx_from_it, current_iterator))
            except StopIteration:
                pass # current_iterator is exhausted
        
        if merged_results:
            if not self.write_sstable(output_sstable_id, merged_results):
                print(f"Failed to write compacted SSTable {output_sstable_id}")
                return False
            # print(f"Compaction successful. New SSTable: {output_sstable_id}")
        else:
            print(f"Compaction resulted in an empty SSTable for {output_sstable_id}.")
        
        return True

    def delete_sstable_files(self, sstable_id: str):
        """Deletes the .dat and .idx files for a given sstable_id."""
        data_path, index_path = self._get_sstable_paths(sstable_id)
        deleted_count = 0
        try:
            if os.path.exists(data_path):
                os.remove(data_path)
                deleted_count+=1
            if os.path.exists(index_path):
                os.remove(index_path)
                deleted_count+=1
            if deleted_count > 0:
                print(f"Deleted SSTable files for ID: {sstable_id}")
        except IOError as e:
            print(f"Error deleting SSTable files for ID {sstable_id}: {e}")