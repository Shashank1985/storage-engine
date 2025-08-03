import os
import json
from .abstract_kv_store import AbstractKVStore


"""
Storage manager class is simple class to check each collection and see the meta data for each collection and then handle teh right function calls for each collection depending on the storage engine for that collection
"""

class StorageManager:
    ENGINE_META_FILE = "engine.meta"

    def __init__(self, base_data_path: str = "data"):
        self.base_data_path = os.path.abspath(base_data_path) # Use absolute path
        os.makedirs(self.base_data_path, exist_ok=True)
        
        self.collections: dict[str, AbstractKVStore] = {} # Stores name -> instance
        self.active_collection_name: str | None = None
        print(f"StorageManager initialized with base path: {self.base_data_path}")

    def _get_collection_path(self, name: str) -> str:
        return os.path.join(self.base_data_path, name)

    def _get_meta_file_path(self, collection_name: str) -> str:
        return os.path.join(self._get_collection_path(collection_name), self.ENGINE_META_FILE)

    def create_collection(self, name: str, engine_type: str = "lsmtree", options: dict = None) -> AbstractKVStore | None:
        if name in self.collections:
            print(f"Error: Collection '{name}' is already loaded in memory.")
            return self.collections[name]

        collection_path = self._get_collection_path(name)
        meta_file_path = self._get_meta_file_path(name)

        if os.path.exists(collection_path):
            if os.path.exists(meta_file_path):
                 print(f"Warning: Data path for collection '{name}' already exists. Attempting to load instead.")
                 return self.load_collection(name) # Try loading if meta exists
            else:
                # Directory exists but no meta file - ambiguous state, could be an old/corrupted setup
                print(f"Error: Data path '{collection_path}' exists but missing '{self.ENGINE_META_FILE}'. Please resolve manually or choose a different name.")
                return None

        os.makedirs(collection_path, exist_ok=True)
        
        options = options if options is not None else {}
        meta_data = {"type": engine_type.lower(), "options": options}
        
        try:
            with open(meta_file_path, 'w') as f:
                json.dump(meta_data, f, indent=2)
        except IOError as e:
            print(f"Error writing meta file for '{name}': {e}")
            # Potentially clean up created directory if meta write fails
            if os.path.exists(collection_path) and not os.listdir(collection_path): # only if empty
                os.rmdir(collection_path)
            return None


        store_instance = self._instantiate_store(collection_path, engine_type.lower(), options)
        print("Created store instance in create_collection")
        if store_instance:
            print(f"DEBUG: StorageManager.create_collection - Calling load() on instance {id(store_instance)}")
            store_instance.load() # Call load even for a new store (it might initialize files)
            print("just after loading in create_collection")
            self.collections[name] = store_instance
            print(f"DEBUG: StorageManager.create_collection - Stored instance {id(self.collections[name])} for '{name}'")
            print(f"Collection '{name}' created successfully with {engine_type} engine.")
            return store_instance
        return None


    def _instantiate_store(self, collection_path: str, engine_type: str, options: dict) -> AbstractKVStore | None:
        if engine_type == "lsmtree":
            from .lsm_tree.lsm_store import LSMTreeStore
            store = LSMTreeStore(collection_path, options)
            print(f"DEBUG: StorageManager._instantiate_store created LSMTreeStore. Instance ID: {id(store)}")
        elif engine_type == "btree":
            # Later: from .b_tree.btree_store import BTreeStore
            # store = BTreeStore(collection_path, options)
            pass # Placeholder for BTreeStore
        else:
            print(f"Error: Unknown engine type '{engine_type}'.")
            return None
        return store


    def load_collection(self, name: str) -> AbstractKVStore | None:
        if name in self.collections:
            return self.collections[name] # Already loaded

        collection_path = self._get_collection_path(name)
        meta_file_path = self._get_meta_file_path(name)

        if not os.path.exists(meta_file_path):
            print(f"Error: Collection '{name}' (meta file not found at '{meta_file_path}'). Cannot load.")
            return None

        try:
            with open(meta_file_path, 'r') as f:
                meta_data = json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            print(f"Error reading or parsing meta file for '{name}': {e}")
            return None
        
        engine_type = meta_data.get("type")
        options = meta_data.get("options", {})

        if not engine_type:
            print(f"Error: 'type' not found in meta file for '{name}'.")
            return None

        store_instance = self._instantiate_store(collection_path, engine_type, options)
        print("just after instantiating store in load_collection", store_instance)
        if store_instance:
            print(f"DEBUG: StorageManager.load_collection - Calling load() on instance {id(store_instance)}")
            store_instance.load() # CRUCIAL: Load persisted state
            print("just after loading in load_collection")
            self.collections[name] = store_instance
            print(f"DEBUG: StorageManager.load_collection - Stored instance {id(self.collections[name])} for '{name}'")
            print(f"Collection '{name}' loaded successfully ({engine_type} engine).")
            return store_instance
        return None


    def use_collection(self, name: str) -> bool:
        if name not in self.collections:
            print(f"DEBUG: StorageManager.use_collection - '{name}' not in self.collections. Attempting load.")
            if not self.load_collection(name): # Attempt to load if not in memory
                print(f"Failed to load or find collection '{name}'.")
                return False
        else:
            print(f"DEBUG: StorageManager.use_collection - '{name}' already in self.collections. Instance ID: {id(self.collections[name])}")
        self.active_collection_name = name
        print(f"Now using collection: '{self.active_collection_name}'")
        return True

    def get_active_collection(self) -> AbstractKVStore | None:
        if self.active_collection_name:
            active_instance = self.collections.get(self.active_collection_name)
            instance_id_str = id(active_instance) if active_instance else "None"
            print(f"DEBUG: StorageManager.get_active_collection - Returning instance {instance_id_str} for '{self.active_collection_name}'") # << ADD
            return active_instance
        print("No active collection. Use 'USE <collection_name>' command.")
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


    def close_all(self) -> None:
        print("Closing all collections...")
        for name, collection_instance in self.collections.items():
            print(f"Closing '{name}'...")
            collection_instance.close()
        self.collections.clear()
        self.active_collection_name = None
        print("All collections closed.")