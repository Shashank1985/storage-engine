# Sharded Memtable Architecture

Starting with version **1.3.0**, BloomKV replaces the single, globally-locked Memtable with a **Sharded Memtable** design. This major architectural shift allows for high-throughput, concurrent writes by significantly reducing lock contention.

## Motivation
In previous versions, the Memtable was protected by a single global `_write_lock`. This meant that even in a multi-threaded environment, only **one thread** could write to the database at a time. All other threads had to wait, effectively serializing operations and underutilizing CPU resources.

## Design overview
The **Sharded Memtable** partitions the in-memory data storage into multiple independent segments (Shards).

* **Partitioning:** The key space is divided into **16 shards** (default).
* **Hashing:** The shard index for any given key is determined using **MurmurHash3** (Reasoning for this choice in BloomFilter.md):
    $$\text{Shard Index} = \text{mmh3.hash(key)} \pmod{\text{Num Shards}}$$
* **Fine-Grained Locking:** Each shard has its own independent `threading.RLock`.
    * Thread A writing key "user:1" (Shard 2) does **not block** Thread B writing "user:2" (Shard 5).
    * Both operations proceed in parallel.

## Operation Workflows

### 1. PUT and DELETE (Write Path)
1.  **Hash:** The system calculates the shard index for the incoming key.
2.  **Lock:** The thread acquires the lock **only** for that specific shard.
3.  **WAL:** The operation is logged to the Write Ahead Log (thread-safe).
4.  **Write:** The key-value pair is inserted into the specific `MemtableShard`.
5.  **Release:** The lock is released immediately.

### 2. GET (Read Path)
1.  **Hash:** The system calculates the shard index.
2.  **Lock:** The thread acquires the lock for that shard.
3.  **Read:** The value is retrieved from the shard's internal sorted dictionary.

### 3. Flush (Disk Persistence)
To ensure data consistency and sorted order in SSTables, flushing requires a synchronized snapshot of all data.

* **Trigger:** When the *aggregated* size of all 16 shards exceeds the threshold (4MB).
* **The "Stop-the-World" Event:**
    1.  The flush thread acquires a global `_flush_lock`.
    2.  It then acquires **all 16 shard locks** sequentially. This briefly blocks all incoming writes.
    3.  **Merge:** Data from all shards is merged into a single sorted list.
    4.  **Write:** The sorted list is written to disk as a standard SSTable.
    5.  **Reset:** All shards are cleared, and locks are released.

## Performance Trade-offs
* **Pros:** Significantly higher write throughput for concurrent workloads.
* **Cons:** Because **Immutable Memtable Pipelining** is not yet implemented, the flush operation causes a periodic latency spike ("Stop-the-World") where writes are blocked until the disk I/O completes.