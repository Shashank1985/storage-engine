"""
Memtable implementation for LSM Tree.
This is an in-memory data structure that stores key-value pairs.
It is used to buffer writes before they are flushed to disk.
It uses a SortedDict to maintain sorted order of keys. Usually it is a self balancing BST, but sortedcontainers gives us the same functionality with a simpler API.
It also tracks the approximate size of the memtable in bytes.
It uses a threshold to determine when to flush the memtable to disk.
It supports basic operations like put, get, delete, and checking if the memtable is full.
It also supports clearing the memtable after flushing.
It uses a tombstone object to represent deleted values.
Data from here is written to the SSTable on disk.
"""

from sortedcontainers import SortedDict
TOMBSTONE = "__TOMBSTONE__" # Unique object to represent a deleted value (tombstone)

class Memtable:
    def __init__(self, threshold_bytes: int = 4 * 1024 * 1024): # Default 4MB threshold
        self._data = SortedDict()
        self.approx_size_bytes = 0 # Approximate size in bytes, starts at 0
        self.threshold_bytes = threshold_bytes # Max size before flush is recommended

    def put(self, key: str, value: str) -> None:
        """Inserts or updates a key-value pair."""
        # Calculate size change
        old_size = 0
        if key in self._data:
            old_val = self._data[key]
            old_size += len(key.encode('utf-8'))
            if old_val is not TOMBSTONE and old_val is not None: # Check if it's not tombstone before encoding
                 old_size += len(str(old_val).encode('utf-8')) # Assuming value is string-like
            
        
        self._data[key] = value
        new_size = len(key.encode('utf-8'))
        if value is not TOMBSTONE and value is not None:
            new_size += len(str(value).encode('utf-8'))
        elif value is TOMBSTONE:
            new_size += 0 # Tombstones are small

        self.approx_size_bytes -= old_size
        self.approx_size_bytes += new_size
        # Ensure size doesn't go negative due to tombstones or complex updates
        self.approx_size_bytes = max(0, self.approx_size_bytes)


    def get(self, key: str): # -> str | object | None (can return TOMBSTONE)
        """Retrieves a value or TOMBSTONE for a key."""
        return self._data.get(key)

    def delete(self, key: str) -> None:
        """Marks a key as deleted by inserting a tombstone."""
        self.put(key, TOMBSTONE) # Use put to handle size calculation

    def is_full(self) -> bool:
        """Checks if the memtable has reached its size threshold."""
        return self.approx_size_bytes >= self.threshold_bytes

    def get_sorted_items(self) -> list[tuple[str, str | object]]:
        """Returns all items sorted by key. Used for flushing to SSTable."""
        # SortedDict iterates in sorted order by default
        return list(self._data.items())

    def clear(self) -> None:
        """Clears the memtable after flushing."""
        self._data.clear()
        self.approx_size_bytes = 0

    def __len__(self) -> int:
        return len(self._data)

    def estimated_size(self) -> int:
        """Returns the current approximate size of the memtable in bytes."""
        return self.approx_size_bytes