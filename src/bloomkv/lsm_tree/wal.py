"""
Write Ahead Log (WAL) implementation. 
This is a log stored in disk, records all the operations to the database, for persistance.
It is used to recover the database in case of a crash or failure.
It is a simple append-only log, where each line was JSON, now it is moved to msgpack binary
"""

import os
import msgpack
import threading

TOMBSTONE = "__TOMBSTONE__"

class WriteAheadLog:
    def __init__(self, wal_path: str):
        self.wal_path = wal_path
        self._file = None
        self._lock = threading.Lock()
        # Open in append mode, create if not exists.
        # The file handle has to be open.
        try:
            self._file = open(self.wal_path, 'ab')
        except IOError as e:
            raise IOError("Error opening the Write Ahead Log")

    def log_operation(self, operation_type: str, key: str, value=None) -> bool:
        """
        Logs an operation to the WAL.
        operation_type: "PUT" or "DELETE"
        value: The value for "PUT", ignored for "DELETE".
        """
        log_entry = {"op": operation_type, "key": key}
        if operation_type == "PUT":
            log_entry["value"] = value
        elif operation_type != "DELETE":
            raise ValueError(f"Unknown operation type '{operation_type}' for WAL.")
       
        with self._lock:
            try:
                packed_entry = msgpack.packb(log_entry)
                self._file.write(packed_entry)
                self._file.flush()  # Ensure it's written to disk immediately for persistence
                os.fsync(self._file.fileno())
            except (IOError, TypeError) as e:
                raise IOError(f"Error writing to WAL {self.wal_path}: {e}")

    def replay(self) -> list[dict]:
        """
        Reads all entries from the WAL and returns them as a list of dicts.
        This is used to reconstruct the memtable on startup.
        """
        entries = []
        if not os.path.exists(self.wal_path):
            return entries

        # Close the current append-mode file handle before reopening in read mode
        with self._lock:
            if self._file and not self._file.closed:
                self._file.close() # Close append mode file

            try:
                with open(self.wal_path, 'rb') as f: # Read binary
                    unpacker = msgpack.Unpacker(f, raw=False)
                    for entry in unpacker:
                        entries.append(entry)
            except Exception as e:
                # Handle possible corruption or empty files
                print(f"Warning: WAL replay issue: {e}")
            finally:
                self._reopen_for_append()
        return entries

    def _reopen_for_append(self):
        # Reopen in append mode
            try:
                self._file = open(self.wal_path, 'ab')
            except IOError as e:
                raise IOError(f"Critical Error: Failed to reopen WAL {self.wal_path} for appending: {e}")
            
    def truncate(self):
        """
        Clears the WAL file. Called after a memtable flush to SSTable is successful.
        """
        with self._lock:
            if self._file and not self._file.closed:
                self._file.close()
            try:
                # Open in write mode to truncate, then immediately reopen in append mode
                with open(self.wal_path, 'wb') as f:
                    pass # Opening in 'w' mode truncates the file
                self._reopen_for_append()
            except IOError as e:
                raise IOError(f"Error truncating WAL file {self.wal_path}: {e}")


    def close(self):
        with self._lock:
            if self._file and not self._file.closed:
                try:
                    self._file.flush() # Final flush
                    self._file.close()
                    
                except IOError as e:
                    raise IOError(f"Error closing WAL file {self.wal_path}: {e}")
            self._file = None

    def __del__(self):
        if self._file and not self._file.closed:
            self.close()