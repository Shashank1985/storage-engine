"""
Memtable implementation for LSM Tree.
Refactored to support Sharding for high concurrency.
Shard the memtable into 16 parts and for each key-value pair, decide which shard it will be stored
once the shard is decided, acquire lock on the shard and write the kv pair to it.
Move from each write obtaining a lock on the entire memtable, which essentially serializes the writes and kills performance
to a concurrent memtable, where each of the 16 threads can run parallely.
"""

from sortedcontainers import SortedDict
import threading
import mmh3
import heapq
from typing import List, Tuple, Union

TOMBSTONE = "__TOMBSTONE__"

class MemtableShard:
    """
    Represents a single partition of the ShardedMemtable.
    Operates like the original Memtable but doesn't manage the global threshold.
    """
    def __init__(self):
        self._data = SortedDict()
        self.approx_size_bytes = 0

    def put(self, key: str, value: str) -> None:
        old_size = 0
        if key in self._data:
            old_val = self._data[key]
            old_size += len(key.encode('utf-8'))
            if old_val is not TOMBSTONE and old_val is not None:
                 old_size += len(str(old_val).encode('utf-8'))
        
        self._data[key] = value
        
        new_size = len(key.encode('utf-8'))
        if value is not TOMBSTONE and value is not None:
            new_size += len(str(value).encode('utf-8'))
        elif value is TOMBSTONE:
            new_size += 0

        self.approx_size_bytes -= old_size
        self.approx_size_bytes += new_size
        self.approx_size_bytes = max(0, self.approx_size_bytes)

    def get(self, key: str):
        return self._data.get(key)

    def delete(self, key: str) -> None:
        self.put(key, TOMBSTONE)

    def get_sorted_items(self) -> list[tuple[str, str | object]]:
        return list(self._data.items())

    def clear(self) -> None:
        self._data.clear()
        self.approx_size_bytes = 0

    def __len__(self) -> int:
        return len(self._data)


class ShardedMemtable:
    """
    A Memtable implementation partitioned into multiple shards to reduce lock contention.
    """
    def __init__(self, threshold_bytes: int = 4 * 1024 * 1024, num_shards: int = 16):
        self.threshold_bytes = threshold_bytes
        self.num_shards = num_shards
        # Create shards
        self.shards = [MemtableShard() for _ in range(num_shards)]
        # Create a lock for each shard
        self.locks = [threading.RLock() for _ in range(num_shards)]

    def get_shard_index(self, key: str) -> int:
        """Determines the shard index for a given key using MurmurHash3."""
        return mmh3.hash(key) % self.num_shards

    def put_to_shard(self, shard_idx: int, key: str, value: str) -> None:
        """Directly puts to a specific shard. Assumes caller holds the shard lock."""
        self.shards[shard_idx].put(key, value)
    
    def put(self, key: str, value: str) -> None:
        """Thread-safe put (handles locking internally)."""
        idx = self.get_shard_index(key)
        with self.locks[idx]:
            self.shards[idx].put(key, value)

    def get(self, key: str):
        """Thread-safe get."""
        idx = self.get_shard_index(key)
        with self.locks[idx]:
            return self.shards[idx].get(key)

    def delete(self, key: str) -> None:
        """Thread-safe delete."""
        self.put(key, TOMBSTONE) # Internal put handles locking

    def is_full(self) -> bool:
        """Checks if the aggregated size of all shards exceeds the threshold."""
        return self.estimated_size() >= self.threshold_bytes

    def estimated_size(self) -> int:
        """Sum of sizes of all shards."""
        return sum(s.approx_size_bytes for s in self.shards)

    def get_sorted_items(self) -> list[tuple[str, str | object]]:
        """
        Merges items from all shards into a single sorted list.
        Used before flushing memtable, acquire all locks on all shards
        NOTE: Caller must ensure global consistency (e.g., hold all shard locks) if concurrent writes are possible.
        """
        # Collect iterators from all shards
        shard_items = [s.get_sorted_items() for s in self.shards]
        # Use heapq.merge to efficiently merge sorted lists
        return list(heapq.merge(*shard_items, key=lambda x: x[0]))

    def clear(self) -> None:
        """Clears all shards."""
        for shard in self.shards:
            shard.clear()

    def __len__(self) -> int:
        return sum(len(s) for s in self.shards)