# Bloom Filter for SSTable searches

The Bloom Filter is a probabilistic data structure integrated into the LSM-tree read path to significantly improve performance by **reducing disk I/O** when searching for non-existent keys.

## Purpose and Mechanism

* **False Negatives (Impossible):** If the Bloom Filter reports that a key is **not present**, the key is guaranteed not to exist in the corresponding SSTable. This saves a disk seek and file scan.
* **False Positives (Possible):** If the filter reports that a key **might be present**, the engine must proceed to check the disk file. The probability of this error is configurable, but generally very low.

## Design and Integration

The Bloom Filter is implemented using the following configuration and integration points:

| Parameter | Value | Description |
| :--- | :--- | :--- |
| **Bits Count ($M$)** | 10,000 | The size of the underlying bit array (~1.25 KB per filter). |
| **Number of Hashes ($K$)** | 3 | The number of independent hash functions used. |
| **Hashing Algorithm** | MurmurHash3 (`mmh3`) | A fast, non-cryptographic hash function optimized for speed and collision resistance. |
| **File Extension** | `.bf` | A dedicated file persisting the filter's bit array is created for each SSTable. |

### 1. Hashing

To ensure three independent hash indices for a given key, the filter uses **double hashing** with two primary MurmurHash3 outputs ($h_1$ and $h_2$):
$$\text{Index}_i(x) = (h_1(x) + i \cdot h_2(x)) \pmod M$$
Where $i$ ranges from 0 to $K-1$.

### 2. Write Path

The filter is created during the Memtable flush process:

1.  When a **Memtable is flushed** and a new SSTable (`.dat`, `.idx`, `.meta`) is written, the Bloom Filter is simultaneously built.
2.  Every unique key being written to the SSTable is **added** to the filter.
3.  The completed bit array is serialized and saved to the corresponding **`.bf`** file on disk.

### 3. Read Path

The Bloom Filter is the first check performed during a key lookup in an SSTable:

1.  The read request first searches the **Memtable**.
2.  If the key is not found in the Memtable, the search moves to the SSTables.
3.  For each SSTable being checked, the system first loads its **`.bf`** file into memory (or checks a cache, if implemented) and queries the filter.
4.  If the filter returns **`False`** (key definitely absent), the disk-based SSTable file is skipped entirely.
5.  If the filter returns **`True`** (key might be present), the system proceeds with the standard disk seek using the sparse index.

This process drastically reduces the latency for read requests that target keys that do not exist, a common operation in many database workloads.

## Acknowledgements

* [A very detailed study of different hash functions, specifically for bloom filters](https://github.com/Baqend/Orestes-Bloomfilter?tab=readme-ov-file#a6)
* [Kirsch-Mitzenmacher Optimization](https://www.eecs.harvard.edu/~michaelm/postscripts/tr-02-05.pdf)

The first link is a very detailed study of different hash functions with regards to bloom filters. The study is conducted keeping 2 points in mind:

1. Computing efficiency
2. Distribution of hash outputs

The author proposes that the Murmur3 hash function is the best for bloom filters trade off wise. He argues that cryptographic hashes like SHA-256 are great when it comes to randomness of outputs but very inefficient to compute. Other hashes like LCG or random number generators are easy to compute but randomness takes a hit. The study ends with the author proving empirically that Murmur3 Hash gives the best compromise. It has great distribution of output and is relatively easy to compute.

The Kirsch-Mitzenmacher Optimization essentially just proves that you only require 2 hash functions to compute k hash functions. The method they introduced in their paper, proves that their function can use the 2 hash functions to generate k more hashes that are seemingly indistinguishable from k independently generated hashes. A simplified version of the method is implemented in this project.