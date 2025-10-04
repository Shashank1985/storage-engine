# LSM-Tree Architecture Overview

## The What and How
This project is a simple **LSM-tree based implementation** of a key-value store in Python. It is explicitly optimized for **write-heavy workloads**.

The core architecture is composed of the three fundamental components of an LSM-tree:
1.  **Write Ahead Log (WAL)**: For atomicity and durability.
2.  **In-memory Memtable**: Where all initial writes are buffered.
3.  **SSTables (Sorted String Tables)**: Persistent storage where items are sorted by keys.

## Data Flow (Write Path)
1.  A write request is received.
2.  The operation is recorded in the **WAL** for crash recovery.
3.  The key-value pair is pushed to the **Memtable** (a sorted container in memory).
4.  Once the Memtable reaches a pre-defined **size threshold** (default 4MB), it is flushed into a new persistent SSTable in Level 0 (L0).
5.  After the flush is complete, the WAL is truncated.

## Data Flow (Read Path)
LSM-Trees are write-optimized, making the read path more complicated compared to B-Trees.

1.  A read request first searches the **Memtable**.
2.  If not found, the search continues to the **SSTables** across various levels. The search prioritizes newer data: L0 (newest to oldest), then L1, L2, and so on.
3. **Using the Sparse Index**: For a given SSTable, the read request first checks the sparse index (`.idx` file) to find the nearest key that is less than or equal to the target key. This index entry provides the **byte offset** in the main data file (`.dat`), allowing the system to skip most of the file and begin scanning from the approximate location of the key, minimizing disk I/O.


## Asynchronous Compaction System (v1.0.0)
The storage engine moved from a simple synchronous model to a **Hybrid, Asynchronous, Read-Optimized model** to address high latency caused by main-thread blocking.
* **Background Worker**: Merging and compaction are handled by a separate, permanent background worker thread. This ensures incoming write requests to the main thread are not blocked.
* **Tiered Compaction (L0)**: When Level 0 (L0) exceeds its capacity (default 4 SSTables), all L0 files are merged and compacted into Level 1 (L1).
* **Leveled Compaction (L1+)**: In levels L1 and above, SSTables are strictly non-overlapping. Compaction finds one SSTable from level Li, determines all overlapping SSTables in Li+1 using key range information (stored in `.meta` files), and merges only those files. This strategy significantly reduces read amplification.