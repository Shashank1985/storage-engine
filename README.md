# **Simple Python Storage Engine**
Light weight key value storage engine optimized for write heavy workloads.

## What and How?
Simple LSM-tree based implementation of a key value store in python. Implemented this after reading chapter 3 of Designing Data Intensive Applications where the author talks about different data structures powering our database, one such data structure was the LSMtree. 
This projects implements a:
1. Write Ahead Log (For atomicity and durability)
2. In memory memtable (Where writes initially go to)
3. SSTables (sorted string tables) where items are sorted based on keys.

The memtable is implemented using the SortedDict library of python. This ensures that the traversal through the memtable will give the items sorted by keys. This is important to flush the memtable in sorted order to the SSTable. 


The SStables are ordered by levels (L0 -> L1 -> ... ). There is a preset number of SSTables allowed within a level, after which the merging and compaction process gets applied and combines those SSTables into one and moves it to the next level. 

The merging and compaction process is a simple k-way merge using a minheap, similar to the leetcode qn (merging k sorted arrays). 

This project supports basic key-value operations like GET, PUT, DELETE, EXISTS, CREATE. A simple CLI based interface is implemented where users can interact with the kv store. 


The writes are first pushed to the Sorted Container in the memtable (which is in memory). Once the memtable reaches a threshold size, it is flushed into a persistent SSTable, every write is recorded in the WAL as it comes, for atomicity and crash recovery. 

The reading process is a bit more complicated. LSMTrees are more optimized for write workloads. When the SSTable gets created, there is an index file also created which stores keys in gaps of 10 (eg: 1st item, 10th item, 20th item etc..). This was implemented for range queries. A read request is first sent to the index file to find which range it lies in and then moves to the different SSTables. Reading is not a very easy process in LSMTrees as compared to B-Trees, which are optimized for read workloads.


## How to install?
pip install lsm_storage_engine_key_value_store

## How to use?
As of current release, 0.3.0, dated 4th Aug, 2025. The package can be used both as a CLI tool and as a library. 

# Usage as Library:
Released as a part of v0.3.0

```python
from lsm_storage_engine import StorageManager
storage = StorageManager(base_data_path=DATA_DIRECTORY)

try:
    # Create and use a collection
    storage.create_collection("products")
    storage.use_collection("products")

    # Get the active collection object
    products_store = storage.get_active_collection()

    # Put and get data
    products_store.put("prod:456", "{'name': 'Super Widget', 'price': 99.99}")
    product = products_store.get("prod:456")

    print(f"Retrieved: {product}")

except CollectionExistsError as e:
    print(f"Error: {e}")
finally:
    # Close all file handles gracefully
    storage.close_all()
```

# Usage as CLI:
Released initially, still supported

run the command lsm-cli.
This will print the cli help with all the functions, follow the instructions as per the cli help.

# General rules:
1. Create collection
2. Use the created collection, if collection already exists, skip step 1 and use the existing collection
3. Currently supports functions like PUT,GET,DELETE,EXISTS
4. Ctrl + C or EXIT to terminate the program.


# Update history:
6th Aug 2025, version 0.4.1: Update Notes: Cleaned up debug and other print statements and logs for public release

## Contribution
Contributions, issues and feature requests are welcome!

## LICENSE
This project is licensed under the MIT License. See the LICENSE file for details.

