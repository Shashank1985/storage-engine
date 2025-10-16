# **Simple Python Storage Engine**
Light weight key value storage engine optimized for write heavy workloads.

## Overview
This project is an **LSM-tree based key-value storage engine** implemented in Python, optimized for **write-heavy workloads**.

It is a persistent key-value store featuring:
* A Write Ahead Log (WAL) for atomicity and durability.
* An in-memory Memtable using `SortedDict`.
* Persistent Sorted String Tables (SSTables).
* Asynchronous, leveled compaction handled by a background worker thread (introduced in v1.0.0).
* Metadata generation for created collections
* Bloom filter functionality to prevent unnecessary disk operations for keys that we know dont exist in the collection
* Storage engine is behind a FastAPI server and can be communicated with using REST APIs.
* Supports range queries from start_key to end_key

## Getting Started
* Detailed documentation and explanation of each component is available in the docs folder.

## Contributing
Contributions, issues and feature requests are welcome!
**Contributing ideas**
* Implement a proper testing suite for different components. Unit tests, Integration Tests
* Thread safety for multi threaded programs
* UI features along with CLI and library utilisation. 
and many more, ideas are always welcome!

## LICENSE
This project is licensed under the MIT License. See the LICENSE file for details.

