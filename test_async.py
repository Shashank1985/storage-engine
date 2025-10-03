import os
import shutil
import time
from lsm_storage_engine import StorageManager, CollectionExistsError

# Use a dedicated folder for test data
TEST_DATA_DIR = "test_data_async"
COLLECTION_NAME = "write_heavy_test"

# --- Start with a clean slate ---
if os.path.exists(TEST_DATA_DIR):
    shutil.rmtree(TEST_DATA_DIR)

storage = StorageManager(base_data_path=TEST_DATA_DIR)

def get_levels_status(store):
    """Utility to check the size of SSTable levels."""
    # Note: Accessing store.levels without a lock here is for monitoring in the test,
    # and the test assumes the compaction thread will eventually finish and release its locks.
    if not store.levels:
        return []
    # Use a list to hold level sizes
    status = []
    for i, level in enumerate(store.levels):
        if level:
            status.append(f"L{i}: {len(level)}")
    return status

try:
    # 1. Create the collection with low thresholds to trigger flushing/compaction
    options = {
        "memtable_threshold_bytes": 1024, # Force flush after ~1-2 writes
        "max_l0_sstables": 3             # Force L0->L1 compaction after 3 flushes
    }
    print(f"Creating collection '{COLLECTION_NAME}' with options: {options}")
    storage.create_collection(COLLECTION_NAME, options=options)
    
    # 2. Set the collection as active (This starts the compaction thread)
    storage.use_collection(COLLECTION_NAME)
    products_store = storage.get_active_collection()

    if products_store:
        NUM_WRITES = 20
        # Put data quickly in the main thread
        print(f"Starting {NUM_WRITES} writes...")
        start_time = time.time()
        for i in range(1, NUM_WRITES + 1):
            key = f"prod:{i:03d}"
            # Ensure value is large enough to quickly exceed the 1024 byte threshold
            value = f"{{'name': 'Item {i}', 'size': 'This is a large payload to ensure memtable flushes quickly. This makes the value string long enough to exceed the small 1KB threshold.'}}"
            products_store.put(key, value)
            if i % 5 == 0:
                print(f"Main thread completed {i} writes. Time: {time.time() - start_time:.4f}s")

        end_write_time = time.time()
        print(f"\nAll {NUM_WRITES} writes complete. Total write time: {end_write_time - start_time:.4f}s")

        # 3. Wait for background compaction to finish
        print("Waiting for compaction to fully stabilize (up to 20 seconds)...")
        
        # Poll the levels until L0 is empty
        for i in range(20): 
            status = get_levels_status(products_store)
            # Compaction is deemed complete when L0 is empty (as L0 is the primary staging level)
            if not status or "L0: 0" in status or (products_store.levels and products_store.levels[0] and len(products_store.levels[0]) == 0):
                print(f"[{i+1}s] Compaction complete. Levels status: {status if status else 'Empty'}")
                break
            print(f"[{i+1}s] Compaction in progress. Levels status: {status}")
            time.sleep(1)
        else:
            print(f"Warning: Compaction did not fully stabilize within 20s. Final status: {get_levels_status(products_store)}")
        
        # 4. Verification: Test all keys are readable after compaction
        print("\nVerifying all keys are present after compaction...")
        verification_success = True
        for i in range(1, NUM_WRITES + 1):
            key = f"prod:{i:03d}"
            retrieved_value = products_store.get(key)
            if retrieved_value is None:
                print(f"Verification FAILED: Key '{key}' not found.")
                verification_success = False
                break
        
        if verification_success:
             print("Verification SUCCESS: All keys found!")
        
    else:
        print("Error: Could not get the active collection store.")

except Exception as e:
    print(f"An unexpected error occurred: {e}")

finally:
    # 5. Clean up
    if storage:
        storage.close_all()
    if os.path.exists(TEST_DATA_DIR):
        shutil.rmtree(TEST_DATA_DIR)
        print("\nCleanup complete.")