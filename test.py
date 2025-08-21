import os
import shutil
from lsm_storage_engine import StorageManager, CollectionExistsError

# Use a dedicated folder for test data
TEST_DATA_DIR = "test_data"

# --- Start with a clean slate ---
if os.path.exists(TEST_DATA_DIR):
    shutil.rmtree(TEST_DATA_DIR)

storage = StorageManager(base_data_path=TEST_DATA_DIR)

try:
    # 1. Create the collection
    storage.create_collection("products")
    
    # 2. ALWAYS set the collection as active
    storage.use_collection("products")

    # 3. Get the active collection object (this will now succeed)
    products_store = storage.get_active_collection()

    # This check prevents the 'NoneType' error
    if products_store:
        # Put and get data
        products_store.put("prod:456", "{'name': 'Super Widget', 'price': 99.99}")
        products_store.put("prod:457", "{'name': 'Super Tablet', 'price': 99.99}")

        product1 = products_store.get("prod:456")
        product2 = products_store.get("prod:457")
        print(f"Retrieved: {product1}")
        print(f"Retrieved: {product2}")
    else:
        print("Error: Could not get the active collection store.")

    #storage.close_collection("products") use this line if you want to close active collection and switch to different one
except Exception as e:
    print(f"An unexpected error occurred: {e}")

finally:
    # Close all file handles gracefully
    if storage:
        storage.close_all()
    
    # Clean up the test directory
    if os.path.exists(TEST_DATA_DIR):
        shutil.rmtree(TEST_DATA_DIR)