"""Batch Packager for Legal Graph Refinement & Evaluation.
Extracts triplet tables and verbatim chunk text from the local SQLite store and
raw chunk directory, partitioning them into compact, self-contained evaluation batches.

Usage:
    python scripts/package_evaluation_batch.py --list-docs                  # View available documents & chunk counts
    python scripts/package_evaluation_batch.py --doc-idx 0                  # Package document at index 0
    python scripts/package_evaluation_batch.py --regulator RBI --max-chunks 30  # Package 30 chunks from RBI
"""

import os
import sys
import json
import sqlite3
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional

# Base paths
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "extraction_store.db"
OUTPUT_DIR = DATA_DIR / "eval_batches"


def get_db():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found at: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def list_candidate_documents(regulator: Optional[str] = None, min_chunks: int = 10, max_chunks: int = 80):
    conn = get_db()
    c = conn.cursor()
    query = """
        SELECT d.document_id, d.regulator, d.document_title, d.total_chunks,
               COUNT(r.relationship_id) as total_relationships
        FROM documents d
        LEFT JOIN relationships r ON d.document_id = r.document_id
        WHERE d.total_chunks BETWEEN ? AND ?
    """
    params = [min_chunks, max_chunks]
    if regulator:
        query += " AND d.regulator = ?"
        params.append(regulator)

    query += " GROUP BY d.document_id ORDER BY total_relationships DESC LIMIT 25;"
    c.execute(query, tuple(params))
    rows = c.fetchall()
    conn.close()

    print("\n" + "=" * 95)
    print(f"{'Idx':<4} | {'Reg':<5} | {'Chunks':<7} | {'Triplets':<9} | {'Document Title'}")
    print("-" * 95)
    for idx, r in enumerate(rows):
        title = r["document_title"] or r["document_id"]
        print(f"{idx:<4} | {r['regulator']:<5} | {r['total_chunks']:<7} | {r['total_relationships']:<9} | {title[:60]}")
    print("=" * 95 + "\n")
    return rows


def package_document_batch(document_id: str, batch_name: Optional[str] = None) -> Path:
    conn = get_db()
    c = conn.cursor()

    # 1. Fetch document metadata
    c.execute("SELECT * FROM documents WHERE document_id = ?", (document_id,))
    doc_row = c.fetchone()
    if not doc_row:
        conn.close()
        raise ValueError(f"Document ID '{document_id}' not found in database.")

    # 2. Fetch all chunks for this document
    c.execute("""
        SELECT chunk_id, document_id, file_path, relative_path, chunk_index,
               section_locator, heading_hierarchy, char_count
        FROM manifest_chunks
        WHERE document_id = ?
        ORDER BY chunk_index ASC;
    """, (document_id,))
    chunk_rows = c.fetchall()

    # 3. Read verbatim chunk content from disk
    chunks_data = []
    chunk_id_set = set()
    for row in chunk_rows:
        cid = row["chunk_id"]
        chunk_id_set.add(cid)
        fpath = Path(row["file_path"])
        if not fpath.is_absolute():
            fpath = ROOT_DIR / fpath

        raw_text = ""
        if fpath.exists():
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as fp:
                    raw_text = fp.read().strip()
            except Exception as ex:
                raw_text = f"[ERROR reading file: {ex}]"

        chunks_data.append({
            "chunk_id": cid,
            "chunk_index": row["chunk_index"],
            "section_locator": row["section_locator"],
            "heading_hierarchy": row["heading_hierarchy"],
            "char_count": row["char_count"],
            "raw_text": raw_text,
        })

    # 4. Fetch existing entities for this document
    c.execute("""
        SELECT entity_id, chunk_id, local_entity_id, mention, canonical_name,
               entity_type, description, identity_scope
        FROM entities
        WHERE document_id = ?;
    """, (document_id,))
    entities = [dict(r) for r in c.fetchall()]

    # 5. Fetch existing relationships for this document
    c.execute("""
        SELECT 
            r.relationship_id,
            r.chunk_id,
            r.local_relationship_id,
            e_src.canonical_name AS subject,
            e_src.entity_type AS subject_type,
            r.relationship_type AS predicate,
            e_tgt.canonical_name AS object,
            e_tgt.entity_type AS object_type,
            r.description,
            r.conditions_json,
            r.exceptions_json,
            r.numeric_constraints_json,
            r.temporal_qualifiers_json,
            r.evidence_json
        FROM relationships r
        LEFT JOIN entities e_src ON r.source_entity_id = e_src.entity_id
        LEFT JOIN entities e_tgt ON r.target_entity_id = e_tgt.entity_id
        WHERE r.document_id = ?;
    """, (document_id,))
    relationships = []
    for r in c.fetchall():
        row_dict = dict(r)
        # Parse JSON fields safely
        for field in ["conditions_json", "exceptions_json", "numeric_constraints_json", "temporal_qualifiers_json", "evidence_json"]:
            if row_dict.get(field):
                try:
                    row_dict[field] = json.loads(row_dict[field])
                except Exception:
                    pass
        relationships.append(row_dict)

    conn.close()

    # Map relationships by chunk_id
    triplets_by_chunk = {}
    for rel in relationships:
        cid = rel["chunk_id"]
        triplets_by_chunk.setdefault(cid, []).append(rel)

    # Clean filename
    doc_slug = doc_row["doc_stem"] or doc_row["document_id"].replace("::", "_").replace(" ", "_")
    safe_name = batch_name or f"eval_batch_{doc_row['regulator']}_{doc_slug[:40]}"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / f"{safe_name}.json"

    batch_payload = {
        "batch_id": safe_name,
        "document_id": document_id,
        "document_title": doc_row["document_title"],
        "regulator": doc_row["regulator"],
        "total_chunks": len(chunks_data),
        "total_existing_entities": len(entities),
        "total_existing_triplets": len(relationships),
        "chunks": chunks_data,
        "entities": entities,
        "relationships": relationships,
        "triplets_by_chunk": triplets_by_chunk,
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(batch_payload, f, indent=2, ensure_ascii=False)

    size_kb = out_file.stat().st_size / 1024.0
    print(f"Packaged evaluation batch to: {out_file} ({size_kb:.1f} KB)")
    print(f"  - Document:   {doc_row['document_title']}")
    print(f"  - Chunks:     {len(chunks_data)}")
    print(f"  - Entities:   {len(entities)}")
    print(f"  - Triplets:   {len(relationships)}")
    return out_file


def main():
    parser = argparse.ArgumentParser(description="Package Knowledge Graph evaluation batches.")
    parser.add_argument("--list-docs", action="store_true", help="List candidate documents with chunk & triplet counts.")
    parser.add_argument("--regulator", type=str, default=None, help="Filter by regulator (MCA, RBI, SEBI).")
    parser.add_argument("--doc-idx", type=int, default=None, help="Index from list-docs to package.")
    parser.add_argument("--doc-id", type=str, default=None, help="Exact document_id to package.")
    parser.add_argument("--batch-name", type=str, default=None, help="Custom filename prefix.")

    args = parser.parse_args()

    if args.list_docs or args.doc_idx is not None:
        docs = list_candidate_documents(regulator=args.regulator)
        if args.doc_idx is not None:
            if 0 <= args.doc_idx < len(docs):
                target_doc_id = docs[args.doc_idx]["document_id"]
                package_document_batch(target_doc_id, batch_name=args.batch_name)
            else:
                print(f"Error: Invalid doc-idx {args.doc_idx}. Must be 0 to {len(docs)-1}.")
        return

    if args.doc_id:
        package_document_batch(args.doc_id, batch_name=args.batch_name)
        return

    # Default action: list documents
    list_candidate_documents(regulator=args.regulator)


if __name__ == "__main__":
    main()
