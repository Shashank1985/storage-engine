"""
Write Ahead Log (WAL) implementation. 
This is a log stored in disk, records all the operations to the database, for persistance.
It is used to recover the database in case of a crash or failure.
It is a simple append-only log, where each line is a JSON object representing an operation.
"""

import os
import json

# A unique object to represent a deleted value (tombstone)
TOMBSTONE = "__TOMBSTONE__"

class WriteAheadLog:
    def __init__(self, wal_path: str):
        self.wal_path = wal_path
        self._file = None
        
        # Open in append mode, create if not exists.
        # The file handle will be kept open for performance.
        try:
            self._file = open(self.wal_path, 'a', encoding='utf-8')
        except IOError as e:
            # Handle error appropriately in a real system (e.g., raise custom exception)
            print(f"Error opening WAL file {self.wal_path}: {e}")
            raise

    def log_operation(self, operation_type: str, key: str, value=None) -> bool:
        """
        Logs an operation to the WAL.
        operation_type: "PUT" or "DELETE"
        value: The value for "PUT", ignored for "DELETE".
        """
        log_entry = {"op": operation_type, "key": key}
        if operation_type == "PUT":
            if value is TOMBSTONE: # Should not happen if API is used correctly
                print("Warning: Attempting to PUT a TOMBSTONE directly via log_operation. This is unusual.")
            log_entry["value"] = value
        elif operation_type == "DELETE":
            pass # No value needed for delete, implies tombstone
        else:
            print(f"Error: Unknown operation type '{operation_type}' for WAL.")
            return False

        try:
            json_entry = json.dumps(log_entry)
            self._file.write(json_entry + '\n')
            self._file.flush()  # Ensure it's written to disk immediately for persistence
            return True
        except (IOError, TypeError) as e:
            print(f"Error writing to WAL {self.wal_path}: {e}")
            # In a real system, you might try to re-open the file or enter a read-only/error state.
            return False

    def replay(self) -> list[dict]:
        """
        Reads all entries from the WAL and returns them as a list of dicts.
        This is used to reconstruct the memtable on startup.
        """
        entries = []
        if not os.path.exists(self.wal_path):
            return entries

        # Close the current append-mode file handle before reopening in read mode
        current_pos = 0
        if self._file and not self._file.closed:
            current_pos = self._file.tell() # Save position if needed, though replay usually means full read
            self._file.close() # Close append mode file

        try:
            with open(self.wal_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entry = json.loads(line)
                            entries.append(entry)
                        except json.JSONDecodeError as e:
                            print(f"Warning: Corrupt WAL entry skipped in {self.wal_path}: '{line[:50]}...' - Error: {e}")
                            # Decide how to handle corruption: stop, skip, log
        except IOError as e:
            print(f"Error reading WAL file {self.wal_path} during replay: {e}")
        finally:
            # Reopen in append mode
            try:
                self._file = open(self.wal_path, 'a', encoding='utf-8')
                if current_pos and self._file: # Seek back if needed (though not typical for simple WAL replay)
                    pass # self._file.seek(current_pos) - usually not needed for append after full read
            except IOError as e:
                print(f"Error re-opening WAL file {self.wal_path} in append mode after replay: {e}")
                # Critical error, the WAL might be unusable for future writes
                
        return entries

    def truncate(self):
        """
        Clears the WAL file. Called after a memtable flush to SSTable is successful.
        """
        if self._file and not self._file.closed:
            self._file.close()
        try:
            # Open in write mode to truncate, then immediately reopen in append mode
            with open(self.wal_path, 'w', encoding='utf-8') as f:
                pass # Opening in 'w' mode truncates the file
            self._file = open(self.wal_path, 'a', encoding='utf-8')
            print(f"WAL {self.wal_path} truncated.")
        except IOError as e:
            print(f"Error truncating WAL file {self.wal_path}: {e}")
            # This is a problem, as old operations might be replayed incorrectly.
            # Try to reopen in append mode anyway if truncation fails but file exists.
            if os.path.exists(self.wal_path) and (not self._file or self._file.closed):
                 try:
                    self._file = open(self.wal_path, 'a', encoding='utf-8')
                 except IOError as e_reopen:
                     print(f"Critical Error: Failed to reopen WAL {self.wal_path} after truncation attempt: {e_reopen}")


    def close(self):
        if self._file and not self._file.closed:
            try:
                self._file.flush() # Final flush
                self._file.close()
                print(f"WAL {self.wal_path} closed.")
            except IOError as e:
                print(f"Error closing WAL file {self.wal_path}: {e}")
        self._file = None

    def __del__(self):
        self.close()