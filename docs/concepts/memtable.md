# Memtable

The Memtable is the in-memory, write-optimized data structure that buffers incoming write requests.

## Design and Function
* **In-Memory Store**: It holds all recent key-value pairs before they are persisted to disk.
* **Sorted Data Structure**: It is implemented using the **`SortedDict`** library. This is a crucial choice because it maintains the sorted order of keys in memory, which allows the Memtable to be flushed to an SSTable in a sorted, contiguous fashion.
* **Size Tracking**: The class tracks the `approx_size_bytes`. This size calculation is used to determine when the Memtable is full (`is_full()`).
* **Threshold**: The default flush threshold is **4MB**. Once this threshold is met, the Memtable is flushed to a new L0 SSTable.
* **Deletions**: Delete operations are handled by inserting a unique sentinel value, `__TOMBSTONE__`, for the specified key.