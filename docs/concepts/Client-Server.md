# Client/Server Architecture

Starting with version **1.2.0**, the LSM Storage Engine supports a Client/Server operational model. This architecture allows the core database logic to run as a single, persistent server process, while client applications (like the CLI or other programs) interact with it via a **RESTful API**.

This approach provides several key advantages:
1.  **Concurrency:** Multiple clients can simultaneously connect to and query the database.
2.  **Persistence:** The server runs continuously, handling background tasks like **asynchronous compaction** without interruption from client disconnects.
3.  **Language Agnostic Access:** Any language capable of making HTTP requests can use the database.

## Usage
Please check usage docs/usage.md for detailed information about how to use the engine.

## Key Server endpoints
The FastAPI server exposes endpoints for both collection management and key-value operations:

| Category | Endpoint | Method | Action |
| :--- | :--- | :--- | :--- |
| Management | `/collection/create` | `POST` | Creates a new collection and stores its metadata. |
| Management | `/collection/use` | `POST` | Sets a collection as the active store for subsequent KV operations. |
| Management | `/collection/meta` | `GET` | Retrieves metadata (including kv_pair_count) for the active collection. |
| Key-Value | `/kv/put` | `POST` | Executes `active_store.put(key, value)`. |
| Key-Value | `/kv/get/{key:path}` | `GET` | Executes `active_store.get(key)`. |
| Key-Value | `/kv/delete` | `POST` | Executes `active_store.delete(key)`. |

## Command Mapping
Every user command entered in the CLI is translated into a corresponding HTTP request to the FastAPI server:

| CLI Command | HTTP Method | Server Endpoint | Example request data |
| :--- | :--- | :--- | :--- |
| USE Products | `POST` | `/collection/use` | {"name": "products"} |
| PUT k v  | `POST` | `/kv/put` | {"key": "k", "value": "v"} |
| GET k | `GET` | `/kv/get/{k}` | (Key is URL-encoded in the path) |
| Delete k | `POST` | `/kv/delete` | {"key": "k"} |
