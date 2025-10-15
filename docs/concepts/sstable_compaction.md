# SSTable and Compaction

SSTables (Sorted String Tables) are the persistent, immutable storage files, and Compaction is the background process that merges them.

## SSTable Structure
* **Data Files (`.mpk`)**: These files store the key value pairs in binary format using msgpack library. Migrated from json files in v1.2.2 This was done to minimize disk storage utilization and decrease load on CPU. Handling json and serializeing/deserialzing them are CPU intensive processes, which is effectively mitigated by migration to msgpack binary.
* **Sparse Index (`.idx`)**: A sparse index is generated when the SSTable is written, storing every 10th key and its corresponding byte offset in the data file. This allows for efficient lookups by minimizing disk seeks for range queries.
* **Metadata Files (`.meta`)**: These files store the `min_key` and `max_key` of the SSTable. This information is essential for the leveled compaction strategy to quickly determine file overlaps.
* **Levels**: SSTables are organized into levels, from L0 (newest) up to higher levels.

## Compaction and Merging
Compaction is a background process that combines multiple SSTables into a single, larger SSTable, removing duplicate and deleted (tombstone) keys in the process.

* **Algorithm**: The merge uses a **k-way merge** algorithm, implemented with a min-heap (similar to merging k sorted lists).
* **Compaction Policy**:
    * **L0 Compaction (Tiered)**: When the number of SSTables in L0 reaches a limit (default 4), all files in L0 are merged into one or more files in L1.
    * **Leveled Compaction (L1+)**: An oldest file from level Li is chosen, and it is merged *only* with the files in Li+1 that have an **overlapping key range**. This ensures higher-level SSTables remain non-overlapping, which drastically improves read performance by allowing key lookup to be directed to a single file per level.
* **Cleanup**: Once the merge is complete and the `MANIFEST` file is updated, the old input SSTables are safely deleted.