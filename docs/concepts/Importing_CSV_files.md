## High Performance importing of CSV files into BloomKV collection
The CSV Bulk Import feature is designed for high-throughput ingestion of large tabular datasets directly into a BloomKV collection. It maps the columns of a CSV file into the required single key and serialized value format of the LSM-tree. To achieve maximum speed, this feature bypasses the standard transactional write path (Write-Ahead Log and Memtable) and uses concurrent disk writes.

## Usage (Library API)
The `import_csv` method is available on the `LSMTreeStore` instance (retrieved via `StorageManager.get_active_collection()`).

```python
def import_csv(
    self, 
    csv_file_path: str, 
    key_columns: List[str], 
    value_columns: List[str], 
    csv_delimiter: str = ",",
    key_separator: str = "\x00",
    chunk_size: int = 100000, 
    max_workers: int = 8 
) -> int:
```
|Parameter|Type|Description|
|---------|----|-----------|
|`csv_file_path`|`str`|Path to the CSV file (must include a header row).|
|`key_columns`|`List[str]`|MANDATORY: List of column names to concatenate and use as the unique, sortable key.|
|`value_columns`|`List[str]`|MANDATORY: List of columns to serialize into the value object.|
|`csv_delimiter`|`str`|The character used to separate fields in the CSV file (default: ,).|
|`key_seperator`|`str`|Internal: The separator used to join the key_columns. Defaults to the Null Character (\x00) for robustness and sort stability.|
|`chunk_size`|`int`|The number of rows to process in memory before sorting and writing a single SSTable. Crucial for memory management of large files (default: 100,000).|
|`max_workers`|`int`|The maximum number of worker threads used for concurrent disk I/O when writing SSTables (default: 8).|


## Architecture and Write Path

To achieve high performance, the bulk import feature operates outside the standard LSM-tree transactional flow.

1. **WAL Bypass**: The import process skips the Write-Ahead Log (WAL) and the Memtable entirely. This eliminates the synchronous disk flush (fsync) overhead that would otherwise be required for every row, which is the largest bottleneck in bulk loading.

2. **Concurrent SSTable Creation**:

    * The main thread performs the CPU-intensive tasks: reading the CSV, transforming the data, and in-memory sorting of each chunk. Cant do multithreading in this step because of GIL limitations, no actual gains

    * Once a chunk is sorted, it is submitted to a ThreadPoolExecutor. This worker thread handles the I/O-bound task of writing the full SSTable files (.mpk, .idx, .meta, .bf) to disk.

3. L0 Placement: Each chunk is written as a new, immutable SSTable in Level 0 (L0).

## Engineering Decisions
**Data Formatting**

|Decision|Rationale|
|---------|---------|
|Key Format: Concatenated String| The key must be a simple string to ensure fast lexicographical sorting and comparison during I/O and in-memory operations. Using the raw string avoids the CPU cost of deserializing a MsgPack object for every comparison.|
|Value Format: MsgPack Dictionary|The value columns are serialized into a dictionary and packed into MsgPack binary. This is the most storage-efficient and fastest format for retrieving the data from SSTables, while preserving the column header information.|

**Atomicity and Consistency**

|Decision|Rationale|
|---------|---------|
|Atomic Manifest Commit| The database state is not updated until all SSTable files are successfully written. The final commit involves a single, atomic update to the MANIFEST file, protected by the self._level_lock, which prevents race conditions with the background compaction thread.|
|Layered Rollback Mechanism|If any I/O error occurs during the concurrent writing of any chunk, the process stops. The implementation then explicitly deletes all SSTables that were successfully written up to that point. This ensures a full rollback (0% or 100% commit), preventing corrupted or incomplete data from being registered in the database.|

**Metadata management**

|Decision|Rationale|
|---------|---------|
|Schema Extension|The collection's metadata file (engine.meta) is extended with a "csv_schema" field, storing the original key_columns and value_columns.|
|Dynamic Count Update|The collection's kv_pair_count is updated with the total number of imported rows, ensuring system statistics are accurate immediately after the import completes|
