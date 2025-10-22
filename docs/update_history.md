# Update History (Changelog)

## 6th Aug 2025, version 0.4.1
Update Notes: Cleaned up debug and other print statements and logs for public release.

## 21 Aug 2025, version 0.4.2
Added a close command to close the active collection and use a new one, if by accident user opens some collection they didn't mean to.

## 2 Oct 2025, version 1.0.0
* **Major Change**: Migration to a Hybrid, Asynchronous, Read-Optimized model.
* **Compaction**: Merging and compaction were moved to a background worker thread to prevent blocking incoming write requests on the single main thread.
* **Leveled Compaction**: Implemented leveled compaction, which ensures SSTables within a level (Li+) are strictly non-overlapping, improving read performance and reducing read amplification.

## 2 Oct 2025, version 1.0.7 
* Support metadata generation for each collection. 
* Metadata includes:
    1. User written description for created collection
    2. Key-value pair count
    3. Date & time of created collection
    etc.

## 12 Oct 2025, version 1.1.1 
* Added a bloom filter for supporting fast key searches.

## 14 Oct 2025, version 1.2.0 
* Migrated storage engine to client-server architecture. Storage engine functionality works behind a lightweight FastAPI server and uses REST APIs to communicate.

## 15 Oct 2025, version 1.2.2 
* Migrated sstables storage from json files to msgpack binary format.

## 16 Oct 2025, version 1.2.3
* Added functionality for supporting range queries, where users can simply select the start_key and end_key and the system will retrieve the most latest keys that lie within the range.

## 22 Oct 2025, version 1.2.6
* New name, welcome to BloomKV!