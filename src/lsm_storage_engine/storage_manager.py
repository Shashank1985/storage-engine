import os
import json
from .abstract_kv_store import AbstractKVStore


"""
Storage manager class is simple class to check each collection and see the meta data for each collection and then handle teh right function calls for each collection depending on the storage engine for that collection
"""
class StorageError(Exception):
    """Base exception for storage-related errors."""
    pass

class CollectionExistsError(StorageError):
    """Raised when a collection already exists."""
    pass

class CollectionNotFoundError(StorageError):
    """Raised when a collection cannot be found on disk."""
    pass
class StorageManager:
    ENGINE_META_FILE = "engine.meta"

    def __init__(self, base_data_path: str = "data"):
        self.base_data_path = os.path.abspath(base_data_path) # Use absolute path
        os.makedirs(self.base_data_path, exist_ok=True)
        
        self.collections: dict[str, AbstractKVStore] = {} # Stores name -> instance
        self.active_collection_name: str | None = None

    def _get_collection_path(self, name: str) -> str:
        return os.path.join(self.base_data_path, name)

    def _get_meta_file_path(self, collection_name: str) -> str:
        return os.path.join(self._get_collection_path(collection_name), self.ENGINE_META_FILE)

    def create_collection(self, name: str, engine_type: str = "lsmtree", options: dict = None) -> AbstractKVStore | None:
        if name in self.collections:
            raise CollectionExistsError(f"Collection '{name}' is already loaded in memory.")


        collection_path = self._get_collection_path(name)
        meta_file_path = self._get_meta_file_path(name)

        if os.path.exists(collection_path):
            raise CollectionExistsError(f"Data path for collection '{name}' already exists.")


        os.makedirs(collection_path, exist_ok=True)
        
        options = options if options is not None else {}
        meta_data = {"type": engine_type.lower(), "options": options}
        
        try:
            with open(meta_file_path, 'w') as f:
                json.dump(meta_data, f, indent=2)
        except IOError as e:
            raise IOError(f"Error writing meta file for '{name}': {e}")


        store_instance = self._instantiate_store(collection_path, engine_type.lower(), options)
        if store_instance:
            store_instance.load() # Call load even for a new store (it might initialize files)
            self.collections[name] = store_instance
            return store_instance
        raise StorageError(f"Failed to instantiate store for '{name}' with engine '{engine_type}'.")


    def _instantiate_store(self, collection_path: str, engine_type: str, options: dict) -> AbstractKVStore | None:
        if engine_type == "lsmtree":
            from .lsm_tree.lsm_store import LSMTreeStore
            return LSMTreeStore(collection_path, options)
        elif engine_type == "btree":
            # from .b_tree.btree_store import BTreeStore
            # return BTreeStore(collection_path, options)
            raise NotImplementedError("B-tree engine is not yet implemented.")
        else:
            raise ValueError(f"Unknown engine type '{engine_type}'.")


    def load_collection(self, name: str) -> AbstractKVStore | None:
        if name in self.collections:
            return self.collections[name] # Already loaded

        collection_path = self._get_collection_path(name)
        meta_file_path = self._get_meta_file_path(name)

        if not os.path.exists(meta_file_path):
            raise CollectionNotFoundError(f"Cannot load collection '{name}': meta file not found.")

        try:
            with open(meta_file_path, 'r') as f:
                meta_data = json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            raise StorageError(f"Error reading or parsing meta file for '{name}': {e}")
        
        engine_type = meta_data.get("type")
        options = meta_data.get("options", {})

        if not engine_type:
            raise StorageError(f"'type' not found in meta file for '{name}'.")

        store_instance = self._instantiate_store(collection_path, engine_type, options)
        if store_instance:
            store_instance.load() # CRUCIAL: Load persisted state
            self.collections[name] = store_instance
            return store_instance


    def use_collection(self, name: str) -> bool:
        if name not in self.collections:
            try:
                self.load_collection(name)
            except (StorageError, CollectionNotFoundError):
                raise CollectionNotFoundError(f"Failed to find or load collection '{name}'.")
        
        self.active_collection_name = name
        return True

    def get_active_collection(self) -> AbstractKVStore | None:
        if self.active_collection_name:
            active_instance = self.collections.get(self.active_collection_name)
            return active_instance
        return None

    def list_collections_on_disk(self) -> list[tuple[str, str]]:
        """Lists collections found in the base_data_path."""
        found_collections = []
        if not os.path.exists(self.base_data_path):
            return found_collections
            
        for item_name in os.listdir(self.base_data_path):
            item_path = os.path.join(self.base_data_path, item_name)
            meta_file = os.path.join(item_path, self.ENGINE_META_FILE)
            if os.path.isdir(item_path) and os.path.exists(meta_file):
                try:
                    with open(meta_file, 'r') as f:
                        meta_data = json.load(f)
                        engine_type = meta_data.get("type", "unknown")
                        found_collections.append((item_name, engine_type))
                except (IOError, json.JSONDecodeError):
                    found_collections.append((item_name, "error_reading_meta"))
        return found_collections

    def close_collection(self, name: str) -> bool:
        """
        Closes and unloads a specific collection.
        """
        if name not in self.collections:
            raise CollectionNotFoundError(f"Collection '{name}' is not currently loaded.")

        try:
            self.collections[name].close()
            del self.collections[name]
            if self.active_collection_name == name:
                self.active_collection_name = None
            return True
        except Exception as e:
            raise StorageError(f"Error closing collection '{name}': {e}")
        
    def close_all(self) -> None:
        for name, collection_instance in self.collections.items():
            collection_instance.close()
        self.collections.clear()
        self.active_collection_name = None
