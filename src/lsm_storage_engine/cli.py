# simple_storage_engine/main.py
import requests
import json
import os
import sys
import urllib.parse
from typing import Optional
SERVER_URL = os.environ.get("LSM_SERVER_URL", "http://127.0.0.1:8000")

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
    """Prints the list of available commands."""
    print("\nLSM Storage Engine CLI - Available Commands:")
    print("  CREATE <name> [lsmtree] [description]  - Create a new collection (on server).")
    print("  USE <name>                     - Set an existing collection as active (on server).")
    print("  LIST                           - List all available collections.")
    print("  ACTIVE                         - Show the currently active collection name.")
    print("  CLOSE <name>                   - Close and unload collection from server memory.")
    print("  PUT <key> <value>              - Store key-value.")
    print("  GET <key>                      - Retrieve value by key.")
    print("  DELETE <key>                   - Delete key-value.")
    print("  EXISTS <key>                   - Check if key exists.")
    print("  META                           - Show the metadata for the active collection.")
    print("  HELP                           - Show this help message.")
    print("  EXIT                           - Exit the application.")
    print()

def send_request(method: str, endpoint: str, data: Optional[dict] = None) -> Optional[requests.Response]:
    """
    Helper function to send HTTP requests and handle connection/HTTP errors gracefully.
    """
    url = f"{SERVER_URL}{endpoint}"
    
    try:
        if method == "GET":
            response = requests.get(url)
        elif method == "POST":
            response = requests.post(url, json=data)
        else:
            print(f"Client Error: Unsupported method '{method}' for network communication.")
            return None
        
        # Check for 4xx or 5xx responses
        if not response.ok:
            try:
                # Attempt to extract detailed error message from FastAPI
                error_detail = response.json().get('detail', f"HTTP Error: {response.status_code}")
            except:
                # Fallback to raw text if JSON parsing fails
                error_detail = response.text.strip()
            print(f"Server Error ({response.status_code}): {error_detail}")
            return None # Indicate failure to the calling function
            
        return response
    
    except requests.exceptions.ConnectionError:
        print(f"\nCRITICAL: Connection failed. Is the LSM server running at {SERVER_URL}?")
        print("Please start the server first using 'lsm-server'.")
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected client error occurred: {type(e).__name__}: {e}")
        return None


def get_active_collection_name() -> str | None:
    """Fetches the active collection name from the server to update the prompt."""
    response = send_request("GET", "/collection/active")
    if response and response.status_code == 200:
        return response.json().get("active_collection")
    return None

def main():
    print(LSM_LOGO)
    print(f"LSM Client initialized. Connecting to server at: {SERVER_URL}")
    print("======================================================================================")
    print("REMINDER: Ensure the server is running via 'lsm-server' in a separate terminal.")
    print_cli_help()

    while True:
        # Dynamically fetch the active collection name for the prompt
        active_coll_name = get_active_collection_name()
        active_coll_prompt = f" [{active_coll_name}]" if active_coll_name else ""
        
        try:
            raw_input = input(f"DB{active_coll_prompt}> ").strip()
            if not raw_input:
                continue

            # Use split(" ", 2) to ensure the value part of PUT can contain spaces
            parts = raw_input.split(" ", 2)
            command = parts[0].upper()
            args = parts[1:]

        except (EOFError, KeyboardInterrupt):
            print("\nExiting client...")
            break
        
        # --- Command Handling ---

        if command == "EXIT":
            break
        elif command == "HELP":
            print_cli_help()

        # Collection Management
        elif command == "CREATE":
            if len(args) < 1:
                print("Usage: CREATE <name> [lsmtree|btree] [description]")
                continue
            coll_name = args[0]
            engine = "lsmtree"
            description = ""
            if len(args) >= 2:
                engine_choice = args[1].lower()
                if engine_choice in ["lsmtree", "btree"]:
                    engine = engine_choice
                    if len(args) > 2:
                        description = args[2]
                else:
                    # If the second arg isn't a known engine type, assume it's the description
                    description = args[1]
            
            payload = {"name": coll_name, "engine_type": engine, "description": description}
            response = send_request("POST", "/collection/create", payload)
            if response:
                print(response.json().get('message', 'OK'))
            
        elif command == "USE":
            if not args or len(args) < 1:
                print("Usage: USE <name>")
                continue
            payload = {"name": args[0]}
            response = send_request("POST", "/collection/use", payload)
            if response:
                print(f"Switched to active collection: {response.json().get('active_collection')}")

        elif command == "CLOSE":
            if not args or len(args) < 1:
                print("Usage: CLOSE <name>")
                continue
            payload = {"name": args[0]}
            response = send_request("POST", "/collection/close", payload)
            if response:
                print(response.json().get('message', 'OK'))
            
        elif command == "LIST":
            response = send_request("GET", "/collection/list")
            if response and response.status_code == 200:
                data = response.json()
                collections = data.get("collections", [])
                if not collections:
                    print("No collections found on disk.")
                else:
                    print("Available collections (name, type):")
                    for name, type_ in collections:
                        print(f"  - {name} ({type_})")

        elif command == "ACTIVE":
            if active_coll_name:
                print(f"Currently active collection: {active_coll_name}")
            else:
                print("No collection is currently active. Use 'USE <name>'.")
        
        elif command == "META":
            response = send_request("GET", "/collection/meta")
            if response and response.status_code == 200:
                meta_data = response.json().get('metadata', {})
                if meta_data:
                    print(f"\n--- Metadata for Collection: {meta_data.get('name', 'N/A')} ---")
                    print(f"  Type: {meta_data.get('type', 'N/A')}")
                    print(f"  Description: {meta_data.get('description', 'N/A')}")
                    print(f"  Date Created: {meta_data.get('date_created', 'N/A')}")
                    print(f"  Key-Value Pairs: {meta_data.get('kv_pair_count', 'N/A')}")
                    print(f"  Configuration Options: {meta_data.get('options', 'N/A')}")
                    print("-----------------------------------------")


        # Key-Value Operations
        elif command == "PUT":
            if len(args) < 2:
                print("Usage: PUT <key> <value>")
                continue
            key, value = args[0], args[1]
            payload = {"key": key, "value": value}
            response = send_request("POST", "/kv/put", payload)
            if response:
                print("OK")

        elif command == "GET":
            if not args or len(args) < 1:
                print("Usage: GET <key>")
                continue
            key = args[0]
            # URL-encode the key path segment to handle keys with special characters
            encoded_key = urllib.parse.quote(key, safe="")
            response = send_request("GET", f"/kv/get/{encoded_key}")
            
            if response and response.status_code == 200:
                data = response.json()
                if data.get("status") == "OK":
                    print(f"Value: {data.get('value')}")
                elif data.get("status") == "NOT_FOUND":
                    print("(nil)")
            
        elif command == "DELETE":
            if not args or len(args) < 1:
                print("Usage: DELETE <key>")
                continue
            payload = {"key": args[0]}
            response = send_request("POST", "/kv/delete", payload)
            if response:
                print("OK")

        elif command == "EXISTS":
            if not args or len(args) < 1:
                print("Usage: EXISTS <key>")
                continue
            key = args[0]
            # URL-encode the key path segment
            encoded_key = urllib.parse.quote(key, safe="")
            response = send_request("GET", f"/kv/exists/{encoded_key}")
            
            if response and response.status_code == 200:
                print(response.json().get("exists"))

        else:
            print(f"Unknown command: '{command}'. Type 'HELP' for available commands.")

