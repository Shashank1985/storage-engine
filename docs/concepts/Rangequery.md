# Range Query System

The **Range Query** feature allows users to efficiently retrieve all key-value pairs within a specified, sorted key range. This operation is critical in any LSM-tree based engine and is implemented using a multi-source merge process to ensure high performance and data correctness.

***

## The Range Query Challenge in LSM-Trees

In an LSM-tree, a single logical key-value pair can exist across multiple physical locations: the **Memtable** and various **SSTables** across different levels. For a range query to be correct, it must perform two core tasks:

1.  **Ordering:** Retrieve all results in a single, globally sorted stream, regardless of their source file.
2.  **Version Resolution:** For any duplicate key found in multiple sources, only the **newest version** (the one closest to the Memtable) must be returned.

***

## Core Mechanism: K-Way Merge Iterator

The `range_query` function implements a **k-way merge** algorithm using a **min-heap** (`heapq` in Python) to efficiently combine the sorted streams from all relevant data sources into a single, resolved output stream.

### 1. Source Prioritization

Each data source (Memtable and SSTables) that overlaps with the requested range is assigned an iterator and a unique **Source ID**:

* **Memtable:** Assigned Source ID **0** (newest data).
* **SSTables (L0, L1, L2...):** Assigned incremental Source IDs starting from 1.

The min-heap uses the tuple `(key, source_id, value, iterator)` for sorting, which ensures that for any identical key, the entry with the **lowest Source ID** (the newest version) is processed first.

### 2. Version and Tombstone Resolution

As the heap pops keys in sorted order:

* The first time a key is popped, it is the newest version, and its value is saved.
* Any subsequent (older) versions of that key are seen and **ignored**, fulfilling the version resolution requirement.
* If the newest version's value is the internal `__TOMBSTONE__` marker, the key is skipped and never yielded to the user.

***

## 🚀 Performance Optimizations

The range query utilizes three main optimizations to minimize disk I/O and server resource usage:

### 1. Metadata Pruning

Before creating an iterator for an SSTable, the system checks the file's **metadata (`.meta`) file** to determine its key range (`min_key` to `max_key`). If the SSTable's range does **not overlap** with the requested range `[start_key, end_key]`, the entire file is excluded from the merge process, saving disk reads.

### 2. Sparse Index Seek

For any SSTable included in the merge, the iterator uses the **Sparse Index (`.idx`)** to quickly find the byte offset of the nearest key that is less than or equal to the `start_key`. This allows the file read to skip a large portion of the data file (`.mpk`), beginning the scan only from the approximate start of the requested range, which minimizes latency.

### 3. Server-side Streaming

The LSM storage engine returns the result as a Python **iterator/generator**. The FastAPI server uses a `StreamingResponse` to deliver the results to the client immediately as they are merged. This is crucial for **preventing memory exhaustion** on the server for queries that return millions of records.

***

## Usage (CLI and API)

The range query is exposed to the user through both the Command Line Interface (CLI) and the REST API.

| Interface | Command/Endpoint | Description |
| :--- | :--- | :--- |
| **CLI** | `RANGE <start_key> <end_key>` | Runs the query and processes the streamed JSON output. |
| **API** | `GET /kv/range?start_key=<key>&end_key=<key>` | Returns a streamed `application/json` response. |