import os
import shutil
from bloomkv import StorageManager, CollectionExistsError, CollectionNotFoundError
import string

DATA_DIRECTORY = "bloomkv_test_data"
COLLECTION_NAME_ONE = "test_catalog"
COLLECTION_NAME_TWO = "range_query_test"
def cleanup_data_dir(path):
    """Removes the data directory if it exists."""
    if os.path.exists(path):
        print(f"\nCleaning up data directory: {path}")
        shutil.rmtree(path)
        
def run_library_test():
    """Runs a simple key-value operation test."""
    
    cleanup_data_dir(DATA_DIRECTORY)
    
    print("1. Initializing StorageManager...")
    storage = StorageManager(base_data_path=DATA_DIRECTORY)
    products_store = None

    try:
        print(f"2. Creating 2 collections: '{COLLECTION_NAME_ONE}' (LSM-Tree)")
        storage.create_collection(
            name=COLLECTION_NAME_ONE,
            engine_type="lsmtree",
            description="A temporary collection for testing library import."
        )
        storage.create_collection(
            name=COLLECTION_NAME_TWO,
            engine_type="lsmtree",
            description="A temporary collection for testing range queries."
        )
        print("Testing list_collection_on_disk method")
        print(storage.list_collections_on_disk())

        print(f"3. Setting '{COLLECTION_NAME_ONE}' as active...")
        storage.use_collection(COLLECTION_NAME_ONE)
        products_store = storage.get_active_collection()
        
        if not products_store:
            print("ERROR: Failed to get active collection instance.")
            return

        key1 = "product:101"
        value1 = '{"name": "Ultra Widget", "price": 49.99}'
        print(f"4. PUTting key '{key1}'...")
        products_store.put(key1, value1)
        
        print(f"5. GETting key '{key1}'...")
        retrieved_value = products_store.get(key1)
        
        print(f"   --> Retrieved Value: {retrieved_value}")
        assert retrieved_value == value1
        print("   --> Test Passed: Value matches original.")

        print(f"6. Checking if key '{key1}' EXISTS...")
        exists_result = products_store.exists(key1)
        print(f"   --> Exists: {exists_result}")
        assert exists_result is True
        
        print(f"7. DELETEing key '{key1}'...")
        products_store.delete(key1)

        
        print(f"8. GETting key '{key1}' after deletion...")
        deleted_value = products_store.get(key1)
        print(f"   --> Retrieved Value: {deleted_value}")
        assert deleted_value is None
        print("   --> Test Passed: Key is now (nil).")

        print("9. BATCH PUT: Inserting keys 'a' through 'z'...")
        for i, char in enumerate(string.ascii_lowercase):
            key = char
            # Store the value as a string representation of its index + 1
            value = str(i + 1)
            products_store.put(key, value)
        print("   --> 26 keys inserted.")

        start_key = 'c'
        end_key = 'g'
        print(f"\n10. RANGE QUERY: Searching for keys in range ['{start_key}' to '{end_key}')")
        
        range_iterator = products_store.range_query(start_key, end_key)
        
        results = list(range_iterator) 
        
        expected_keys = ['c', 'd', 'e', 'f'] # 'g' is exclusive
        expected_count = len(expected_keys)
        
        print(f"   --> Expected Keys: {expected_keys}")
        print(f"   --> Retrieved {len(results)} results:")
        
        retrieved_keys = []
        for key, value in results:
            retrieved_keys.append(key)
            print(f"      - {key}: {value}")

        # 6. Validate the Range Query results
        assert len(results) == expected_count, f"Range Query Failed: Expected {expected_count} results, got {len(results)}."
        assert retrieved_keys == expected_keys, f"Range Query Failed: Expected keys {expected_keys}, got {retrieved_keys}."
        print("\n   --> Range Query Test **PASSED**.") 

    except (CollectionExistsError, CollectionNotFoundError) as e:
        print(f"Test Failed (Database Error): {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        print("\n9. Closing all collections...")
        storage.close_all()
        
    cleanup_data_dir(DATA_DIRECTORY)

if __name__ == "__main__":
    run_library_test()