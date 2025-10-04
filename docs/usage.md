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
run the command on terminal

```
lsm-cli
```
This will print the cli help with all the functions, follow the instructions as per the cli help.


# General Rules
1. Create a collection, be sure to give a good description of the collection
2. Use the newly created collection or skip step 1 if collection is already created
3. Currently supports functions like PUT,GET,DELETE,EXISTS
4. Ctrl + C to exit the program or follow the test code for programmatic use