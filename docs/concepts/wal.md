# Write Ahead Log (WAL)

The Write Ahead Log is a critical component for **atomicity and durability** in the storage engine.

## Purpose and Mechanism
* **Persistence**: The WAL is a simple append-only log stored on disk.
* **Recovery**: It records all operations (PUT and DELETE) to the database. In case of a crash, the WAL is replayed on startup to reconstruct the Memtable's state.
* **Operation**: Each line in the WAL is a JSON object representing an operation.

## Key Operations
| Operation | Description |
| :--- | :--- |
| `log_operation` | Writes the operation ("PUT" or "DELETE") and key (and value for "PUT") to the log, ensuring it is immediately flushed to disk. |
| `replay` | Reads all entries from the WAL on startup to restore the in-memory Memtable. |
| `truncate` | Clears the WAL file. This is called only after a successful flush of the Memtable's contents to a persistent SSTable. |