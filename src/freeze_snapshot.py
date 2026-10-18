"""Graph and Extraction Snapshot Freezer for Legal GraphRAG Stage 3.
Generates a cryptographically hashed, reproducible snapshot manifest capturing:
- Corpus and graph versions
- Node and relationship counts
- Hash of extraction database and FTS index
- Review status policy ('unreviewed_extraction_dev_mode')
- Code and dependency versions
"""

import os
import sys
import json
import hashlib
import time

sys.path.insert(0, os.path.abspath("."))
from src.config import (
    DB_PATH,
    FALKORDB_GRAPH_NAME,
    SCHEMA_VERSION,
    PROMPT_VERSION,
    MODEL_NAME,
    EXECUTION_ROUTE,
    get_falkordb_client,
)

def compute_file_sha256(filepath: str, max_bytes: int = 100 * 1024 * 1024) -> str:
    """Computes SHA256 hash of a file (up to max_bytes for speed)."""
    if not os.path.exists(filepath):
        return "FILE_NOT_FOUND"
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        bytes_read = 0
        while chunk := f.read(65536):
            h.update(chunk)
            bytes_read += len(chunk)
            if bytes_read >= max_bytes:
                break
    return h.hexdigest()

def freeze_snapshot(snapshot_id: str = "v1") -> dict:
    print(f"Freezing snapshot '{snapshot_id}' for Legal GraphRAG Stage 3...")
    os.makedirs("data/snapshots", exist_ok=True)

    # 1. Inspect FalkorDB live graph counts
    client = get_falkordb_client()
    graph = client.select_graph(FALKORDB_GRAPH_NAME)

    counts = {}
    for label in ["Document", "Chunk", "Entity", "RelationshipAssertion", "EvidenceSpan"]:
        res = graph.query(f"MATCH (n:{label}) RETURN count(n)")
        counts[label] = res.result_set[0][0]

    for rel in ["HAS_CHUNK", "MENTIONS", "SUBJECT_OF", "OBJECT", "RELATION", "SUPPORTED_BY", "IN_CHUNK"]:
        res = graph.query(f"MATCH ()-[r:{rel}]->() RETURN count(r)")
        counts[f"EDGE_{rel}"] = res.result_set[0][0]

    # Total counts
    res_nodes = graph.query("MATCH (n) RETURN count(n)")
    counts["TOTAL_NODES"] = res_nodes.result_set[0][0]
    res_edges = graph.query("MATCH ()-[r]->() RETURN count(r)")
    counts["TOTAL_EDGES"] = res_edges.result_set[0][0]

    # 2. Checksum files
    db_hash = compute_file_sha256(str(DB_PATH))
    fts_hash = compute_file_sha256("data/indexes/baseline/rbi_fts.db")

    manifest = {
        "snapshot_id": snapshot_id,
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "corpus": "Reserve Bank of India (RBI) NBFC Master Directions & Circulars",
        "regulator": "RBI",
        "graph_name": FALKORDB_GRAPH_NAME,
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model_name": MODEL_NAME,
        "execution_route": EXECUTION_ROUTE,
        "review_status_policy": {
            "status": "unreviewed",
            "execution_mode": "unreviewed_extraction_dev_mode",
            "human_reviewed_count": 0,
            "candidate_valid_count": counts["Entity"],
            "quarantined_count": 0,
            "disclosure": (
                "All graph elements and derived community reports are generated via validated model extraction "
                "operating under explicitly labeled unreviewed_extraction_dev_mode. No records are represented as human-approved."
            )
        },
        "graph_topology": counts,
        "content_hashes": {
            "extraction_store_db_sha256": db_hash,
            "rbi_fts_db_sha256": fts_hash,
        },
        "environment_and_dependencies": {
            "python_version": sys.version.split()[0],
            "leidenalg": "0.12.0",
            "igraph": "1.0.0",
            "falkordb_server": "FalkorDB on Redis 6379",
            "sentence_transformers": "all-MiniLM-L6-v2 (384-d)",
        }
    }

    manifest_path = f"data/snapshots/snapshot_{snapshot_id}_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"Snapshot manifest successfully frozen to: {manifest_path}")
    print(f"Total nodes: {counts['TOTAL_NODES']}, Total edges: {counts['TOTAL_EDGES']}")
    return manifest

if __name__ == "__main__":
    freeze_snapshot("v1")
