from abc import ABC, abstractmethod
import os
from typing import Iterator,Tuple,List



class AbstractKVStore(ABC):
    """
    Abstract class for a key-value store.
    Definnes the interface for a key-value store for different storage implementation.
    """

    def __init__(self, collection_path: str, options: dict = None):
        """
        Initializes the KV store.

        Args:
            collection_path (str): The directory path where this collection's data will be stored.
            options (dict, optional): Engine-specific configuration options. Defaults to None.
        """
        self.collection_path = collection_path
        self.options = options if options is not None else {}
        
        # Ensure the base directory for this collection exists
        os.makedirs(self.collection_path, exist_ok=True)
        print(f"AbstractKVStore initialized for path: {self.collection_path}") # For debugging

    @property
    @abstractmethod
    def key_count(self) -> int:
        """Returns the current number of key-value pairs in the store (for dynamic metadata)."""
        pass 

    @abstractmethod
    def update_metadata(self) -> None:
        """
        Persists dynamic metadata (like key_count) to the collection's meta file.
        Called on store closure or during flushing/compaction.
        """
        pass

    @abstractmethod
    def put(self, key: str, value: str) -> None:
        """Stores or updates a key-value pair."""
        pass

    @abstractmethod
    def get(self, key: str) -> str | None:
        """Retrieves the value for a given key. Returns None if not found."""
        pass

    @abstractmethod
    def delete(self, key: str) -> None:
        """Removes a key-value pair."""
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Checks if a key exists in the store."""
        pass
    
    @abstractmethod
    def range_query(self, start_key: str, end_key: str) -> Iterator[Tuple[str, str]]:
        """
        Retrieves all key-value pairs where start_key <= key < end_key, 
        ensuring only the newest version of each key is returned.
        Returns a generator for memory efficiency.
        """
        pass

    @abstractmethod
    def load(self) -> None:
        """
        Loads the store's state from persistent storage.
        Called when the store is initialized for an existing collection.
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """
        Performs any necessary cleanup, like flushing data to disk.
        Called when the storage engine is shutting down.
        """
        pass

    @abstractmethod
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
        """
        Bulk imports data from a CSV file into the store.
        
        Args:
            csv_file_path (str): Path to the CSV file.
            key_columns (List[str]): List of column names to use for key construction.
            value_columns (List[str]): List of column names to store in the serialized value.
            csv_delimiter (str): The delimiter used in the CSV file.
            key_separator (str): The internal character used to join key columns.
            chunk_size (int): Max size of a chunk, which is created for very large csv files.
            max_workers (int): Thread pool to handle concurrent I/O operations(writing sstables)
            
        Returns:
            int: The number of key-value pairs successfully imported.
        """
        pass