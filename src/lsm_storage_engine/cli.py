# simple_storage_engine/main.py
import os
import json 
from .storage_manager import StorageManager, CollectionExistsError, CollectionNotFoundError, StorageError

LSM_LOGO = """
 ___        ________  ___      ___                      
|"  |      /"       )|"  \    /"  |                     
||  |     (:   \___/  \   \  //   |                     
|:  |      \___  \    /\\  \/.    |                     
 \  |___    __/  \\  |: \.        |                     
( \_|:  \  /" \   :) |.  \    /:  |                     
 \_______)(_______/  |___|\__/|___|                     
                                                        
 __   ___  ___      ___                                 
|/"| /  ")|"  \    /"  |                                
(: |/   /  \   \  //  /                                 
|    __/    \\  \/. ./                                  
(// _  \     \.    //                                   
|: | \  \     \\   /                                    
(__|  \__)     \__/                                     
                                                        
  ________  ___________  ______     _______    _______  
 /"       )("     _   ")/    " \   /"      \  /"     "| 
(:   \___/  )__/  \\__/// ____  \ |:        |(: ______) 
 \___  \       \\_ /  /  /    ) :)|_____/   ) \/    |   
  __/  \\      |.  | (: (____/ //  //      /  // ___)_  
 /" \   :)     \:  |  \        /  |:  __   \ (:      "| 
(_______/       \__|   \"_____/   |__|  \___) \_______) 
                                                        
"""


def print_cli_help():
    print("\nSimple Storage Engine CLI - Available Commands:")
    print("  CREATE <name> [lsmtree|btree] [description]  - Create a new collection (default: lsmtree).")
    print("  USE <name>                     - Switch to an existing collection to make it active.")
    print("  LIST                           - List all available collections on disk.")
    print("  ACTIVE                         - Show the currently active collection.")
    print("  CLOSE <name>                   - Close and unload active collection from memory.")
    print("  PUT <key> <value>              - Store key-value in the active collection.")
    print("  GET <key>                      - Retrieve value by key from active collection.")
    print("  DELETE <key>                   - Delete key-value from active collection.")
    print("  EXISTS <key>                   - Check if key exists in active collection.")
    print("  META                           - Show the metadata for the active collection.")
    print("  HELP                           - Show this help message.")
    print("  EXIT                           - Exit the application.")
    print()

def main():
    data_dir = os.path.join(os.getcwd(), "data")
    manager = StorageManager(base_data_path=data_dir)

    print(LSM_LOGO)    
    print(f"Data will be stored in: {manager.base_data_path}")
    print("======================================================================================")
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
                    print("Usage: CREATE <name> [lsmtree|btree] [description]")
                    continue
                coll_name = args[0]
                engine = "lsmtree" # Default
                description = ""
                if len(args) >= 2:
                    engine_choice = args[1].lower()
                    if engine_choice in ["lsmtree", "btree"]:
                        engine = engine_choice
                        if len(args) > 2:
                            description = args[2]
                    else:
                        description = args[1]
                manager.create_collection(coll_name, engine,description=description)

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
            elif command == "CLOSE":
                if not args or len(args) < 1:
                    print("Usage: CLOSE <name>")
                coll_name = args[0]
                manager.close_collection(coll_name)
                print(f"Collection '{coll_name}' has been successfully closed.")
            elif command == "META": 
                if manager.active_collection_name:
                    coll_name = manager.active_collection_name
                    meta_file_path = manager._get_meta_file_path(coll_name) # Reuse StorageManager helper
                    
                    if os.path.exists(meta_file_path):
                        with open(meta_file_path, 'r') as f:
                            meta_data = json.load(f)
                        
                        print(f"\n--- Metadata for Collection: {coll_name} ---")
                        print(f"  Name: {meta_data.get('name', 'N/A')}")
                        print(f"  Type: {meta_data.get('type', 'N/A')}")
                        print(f"  Description: {meta_data.get('description', 'N/A')}")
                        print(f"  Date Created: {meta_data.get('date_created', 'N/A')}")
                        print(f"  Key-Value Pairs: {meta_data.get('kv_pair_count', 'N/A')}")
                        print(f"  Configuration Options: {meta_data.get('options', 'N/A')}")
                        print("-----------------------------------------")
                    else:
                        print(f"Error: Metadata file not found for collection '{coll_name}'.")
                else:
                    print("No collection is currently active. Use 'USE <name>'.")
            # Commands requiring an active collection
            else:
                active_store = manager.get_active_collection()
                if not active_store:
                    raise CollectionNotFoundError("No active collection. Use 'USE <name>' command.")
                # print(f"DEBUG_MAIN_PY: Before calling put on active_store. Type: {type(active_store)}. ID: {id(active_store)}")
                # if hasattr(active_store, 'wal'):
                #     print(f"DEBUG_MAIN_PY: active_store.wal is None: {active_store.wal is None}")
                # else:
                #     print("DEBUG_MAIN_PY: active_store has no 'wal' attribute")
                # if hasattr(active_store, 'memtable'):
                #     print(f"DEBUG_MAIN_PY: active_store.memtable is None: {active_store.memtable is None}")
                # else:
                #     print("DEBUG_MAIN_PY: active_store has no 'memtable' attribute")
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
        except (CollectionExistsError, CollectionNotFoundError, StorageError, ValueError) as e:
            print(f"Error: {e}")
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            

    manager.close_all()
