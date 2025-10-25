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
|---------|----|-----------|
|`key_columns`|`List[str]`|MANDATORY: List of column names to concatenate and use as the unique, sortable key.|
|---------|----|-----------|
|`value_columns`|`List[str]`|MANDATORY: List of columns to serialize into the value object.|
|---------|----|-----------|
|`csv_delimiter`|`str`|The character used to separate fields in the CSV file (default: ,).|
|---------|----|-----------|
|`key_seperator`|`str`|Internal: The separator used to join the key_columns. Defaults to the Null Character (\x00) for robustness and sort stability.|
|---------|----|-----------|
|`chunk_size`|`int`|The number of rows to process in memory before sorting and writing a single SSTable. Crucial for memory management of large files (default: 100,000).|
|---------|----|-----------|
|`max_workers`|`int`|The maximum number of worker threads used for concurrent disk I/O when writing SSTables (default: 8).|
