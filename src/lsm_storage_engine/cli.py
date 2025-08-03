# simple_storage_engine/main.py
import os
import json 
from .storage_manager import StorageManager

def print_cli_help():
    print("\nSimple Storage Engine CLI - Available Commands:")
    print("  CREATE <name> [lsmtree|btree]  - Create a new collection (default: lsmtree).")
    print("  USE <name>                     - Switch to an existing collection to make it active.")
    print("  LIST                           - List all available collections on disk.")
    print("  ACTIVE                         - Show the currently active collection.")
    print("  PUT <key> <value>              - Store key-value in the active collection.")
    print("  GET <key>                      - Retrieve value by key from active collection.")
    print("  DELETE <key>                   - Delete key-value from active collection.")
    print("  EXISTS <key>                   - Check if key exists in active collection.")
    print("  HELP                           - Show this help message.")
    print("  EXIT                           - Exit the application.")
    print()

def main():
    data_dir = os.path.join(os.getcwd(), "data")
    manager = StorageManager(base_data_path=data_dir)
    
    print("Welcome to LSM Storage Engine CLI!")
    print(f"Data will be stored in: {manager.base_data_path}")
    print_cli_help()

    while True:
        active_coll_prompt = f" [{manager.active_collection_name}]" if manager.active_collection_name else ""
        try:
            raw_input = input(f"DB{active_coll_prompt}> ").strip()
            if not raw_input:
                continue

            parts = raw_input.split(" ", 2) # Max 3 parts for PUT "key" "value"
            command = parts[0].upper()
            args = parts[1:]

        except EOFError:
            print("\nExiting (EOF)...")
            break
        except KeyboardInterrupt:
            print("\nExiting (Interrupt)...")
            break
        
        try:
            if command == "EXIT":
                print("Exiting application...")
                break
            elif command == "HELP":
                print_cli_help()
            elif command == "CREATE":
                if not args or len(args) < 1:
                    print("Usage: CREATE <name> [lsmtree|btree]")
                    continue
                coll_name = args[0]
                engine = "lsmtree" # Default
                if len(args) > 1:
                    engine_choice = args[1].lower()
                    if engine_choice in ["lsmtree", "btree"]:
                        engine = engine_choice
                    else:
                        print(f"Invalid engine type: {args[1]}. Choose 'lsmtree' or 'btree'.")
                        continue
                manager.create_collection(coll_name, engine)

            elif command == "USE":
                if not args or len(args) < 1:
                    print("Usage: USE <name>")
                    continue
                manager.use_collection(args[0])
            
            elif command == "LIST":
                collections_on_disk = manager.list_collections_on_disk()
                if not collections_on_disk:
                    print("No collections found on disk.")
                else:
                    print("Available collections (name, type):")
                    for name, type_ in collections_on_disk:
                        print(f"  - {name} ({type_})")
            
            elif command == "ACTIVE":
                if manager.active_collection_name:
                    print(f"Currently active collection: {manager.active_collection_name}")
                else:
                    print("No collection is currently active. Use 'USE <name>'.")

            # Commands requiring an active collection
            else:
                active_store = manager.get_active_collection()
                if not active_store:
                    # get_active_collection already prints a message
                    continue
                print(f"DEBUG_MAIN_PY: Before calling put on active_store. Type: {type(active_store)}. ID: {id(active_store)}")
                if hasattr(active_store, 'wal'):
                    print(f"DEBUG_MAIN_PY: active_store.wal is None: {active_store.wal is None}")
                else:
                    print("DEBUG_MAIN_PY: active_store has no 'wal' attribute")
                if hasattr(active_store, 'memtable'):
                    print(f"DEBUG_MAIN_PY: active_store.memtable is None: {active_store.memtable is None}")
                else:
                    print("DEBUG_MAIN_PY: active_store has no 'memtable' attribute")
                if command == "PUT":
                    if len(args) < 2:
                        print("Usage: PUT <key> <value>")
                        continue
                    key, value = args[0], args[1] # Value can contain spaces if it's the last arg
                    active_store.put(key, value)
                    print("OK")
                elif command == "GET":
                    if not args or len(args) < 1:
                        print("Usage: GET <key>")
                        continue
                    key = args[0]
                    value = active_store.get(key)
                    if value is not None:
                        print(f"Value: {value}")
                    else:
                        print("(nil)")
                elif command == "DELETE":
                    if not args or len(args) < 1:
                        print("Usage: DELETE <key>")
                        continue
                    active_store.delete(args[0])
                    print("OK")
                elif command == "EXISTS":
                    if not args or len(args) < 1:
                        print("Usage: EXISTS <key>")
                        continue
                    if active_store.exists(args[0]):
                        print("True")
                    else:
                        print("False")
                else:
                    print(f"Unknown command: '{command}'. Type 'HELP' for available commands.")
        
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            

    manager.close_all()
    print("Application shut down gracefully.")
