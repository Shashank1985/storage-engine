# Usage Guide

## How to install?
```bash
pip install bloomkv
```

You can also pull and run the docker image of the server
```bash
docker run -d -p 8000:8000 -v /local/data/path:/app/bloomkv_server_data --name bloomkv sensei0410/bloomkv:1.2.8
```
Running this command will start the bloomkv server in a docker container
## How to use?
As of current release, **1.2.3**, the package can be used both as a CLI tool and as a library. 

# Usage as Library:
Released as a part of v0.3.0
please check storage-engine/bloomkv-test.py file for the usage as a library and how to integrate it into your project

# Usage as CLI
You will require 2 seperate terminals.


Run this command on one terminal to start the server
```
bloomkv-server
```
* **Default Behaviour**: The server starts on http://127.0.0.1:8000.
* **Data Path**:  It stores all collection data in a directory (default: lsm_server_data in the current working directory).
* **Graceful Shutdown**: On shutdown (e.g., via Ctrl+C), the server ensures all active collections are gracefully closed and flushed to disk.

Run this command on another terminal to start the client cli tool
```
bloomkv-cli
```
This will first connect to the server and then print the cli help. Follow the instructions given in the cli help.

# General Rules
1. Create a collection, be sure to give a good description of the collection
2. Use the newly created collection or skip step 1 if collection is already created
3. Currently supports functions like PUT,GET,DELETE,EXISTS
4. Ctrl + C to exit the program or follow the test code for programmatic use
