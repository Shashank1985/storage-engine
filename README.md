# **Simple Python Storage Engine**

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
ork as per prompts on the CLI
>>>>>>> ebcd82836ed2ddce9cadd3478018d028b979a606
