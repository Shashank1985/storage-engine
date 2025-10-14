# Collection Metadata
The Metadata component is essential for managing the configuration and tracking the status of each individual key-value store (Collection). It was officially supported starting in version **1.0.7**.

## Purpose and Storage

Collection metadata serves two primary purposes:
1.  **Configuration:** Storing the parameters needed to correctly load and instantiate the storage engine when the collection is re-opened.
2.  **Statistics:** Tracking dynamic information, such as the total number of key-value pairs.

### Storage Location
All collection metadata is stored in a dedicated JSON file named `engine.meta`. This file resides in the root directory of its respective collection.

## Metadata Fields

The metadata is composed of static fields (written once during creation) and dynamic fields (updated during operation).
### Static Fields (Written on `create_collection`)
These fields are initialized when a new collection is created and typically do not change:

| Field Key | Type | Description |
| :--- | :--- | :--- |
| `name` | String | The unique name of the collection. |
| `type` | String | The storage engine type (e.g., `"lsmtree"`). |
| `description` | String | A user-provided description for the collection. |
| `date_created` | String | The ISO 8601 timestamp indicating when the collection was first created. |
| `options` | Dictionary | Engine-specific configuration options passed during creation (e.g., custom Memtable threshold). |

### Dynamic Field (Updated During Operation)

| Field Key | Type | Description |
| :--- | :--- | :--- |
| `kv_pair_count` | Integer | The total number of unique, non-deleted key-value pairs in the collection. |


## Accessing Metadata

Users can view the metadata for the currently active collection through the CLI:
```bash
DB [my_collection]> META
```
This command triggers a request to the server's `/collection/meta` endpoint, which ensures the latest key count is persisted before reading and displaying the data.






