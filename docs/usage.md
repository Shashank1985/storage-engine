# Usage Guide

## How to install?
pip install lsm_storage_engine_key_value_store

## How to use?
As of current release, **1.1.0**, the package can be used both as a CLI tool and as a library. 

# Usage as Library:
Released as a part of v0.3.0

```python
from lsm_storage_engine import StorageManager
storage = StorageManager(base_data_path=DATA_DIRECTORY)

try:
    # Create and use a collection
    storage.create_collection("products",description = "Test description")
    storage.use_collection("products")

    # Get the active collection object
    products_store = storage.get_active_collection()

    # Put and get data
    products_store.put("prod:456", "{'name': 'Super Widget', 'price': 99.99}")
    product = products_store.get("prod:456")

    print(f"Retrieved: {product}")

except CollectionExistsError as e:
    print(f"Error: {e}")
finally:
    # Close all file handles gracefully
    storage.close_all()
```

# Usage as CLI
You will require 2 seperate terminals.


Run this command on one terminal to start the server
```
lsm-server
```
* **Default Behaviour**: The server starts on http://127.0.0.1:8000.
* **Data Path**:  It stores all collection data in a directory (default: lsm_server_data in the current working directory).
* **Graceful Shutdown**: On shutdown (e.g., via Ctrl+C), the server ensures all active collections are gracefully closed and flushed to disk.

Run this command on another terminal to start the client cli tool
```
lsm-cli
```
This will first connect to the server and then print the cli help. Follow the instructions given in the cli help.

# General Rules
1. Create a collection, be sure to give a good description of the collection
2. Use the newly created collection or skip step 1 if collection is already created
3. Currently supports functions like PUT,GET,DELETE,EXISTS
4. Ctrl + C to exit the program or follow the test code for programmatic use