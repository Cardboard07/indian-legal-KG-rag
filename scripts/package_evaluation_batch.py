"""Batch Packager for Legal Graph Refinement & Evaluation.
Extracts triplet tables and verbatim chunk text from the local SQLite store and
raw chunk directory, partitioning them into compact, bounded, self-contained evaluation batches.

Usage:
    # 1. View candidate documents with chunk & triplet counts
    python scripts/package_evaluation_batch.py --list-docs --regulator RBI

    # 2. Package a single document by index or ID (sliced into 50-chunk parts)
    python scripts/package_evaluation_batch.py --doc-idx 0 --chunk-slice-size 50
    python scripts/package_evaluation_batch.py --doc-id "RBI::MasterDirection::DNBR_001"

    # 3. Package all documents for a regulator into bounded batches
    python scripts/package_evaluation_batch.py --all --regulator RBI --chunk-slice-size 50
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
DEFAULT_OUTPUT_DIR = DATA_DIR / "eval_batches"


def get_db():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found at: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def list_candidate_documents(
    regulator: Optional[str] = None,
    min_chunks: int = 1,
    max_chunks: int = 10000,
    min_triplets: int = 1,
    limit: Optional[int] = 50,
) -> List[sqlite3.Row]:
    conn = get_db()
    c = conn.cursor()
    query = """
        SELECT d.document_id, d.regulator, d.document_title, d.total_chunks,
               COUNT(r.relationship_id) as total_relationships
        FROM documents d
        LEFT JOIN relationships r ON d.document_id = r.document_id
        WHERE d.total_chunks BETWEEN ? AND ?
    """
    params: List[Any] = [min_chunks, max_chunks]
    if regulator:
        query += " AND d.regulator = ?"
        params.append(regulator)

    query += " GROUP BY d.document_id HAVING total_relationships >= ?"
    params.append(min_triplets)

    query += " ORDER BY total_relationships DESC"
    if limit:
        query += f" LIMIT {limit}"
    query += ";"

    c.execute(query, tuple(params))
    rows = c.fetchall()
    conn.close()

    print("\n" + "=" * 95)
    print(f"{'Idx':<4} | {'Reg':<5} | {'Chunks':<7} | {'Triplets':<9} | {'Document Title'}")
    print("-" * 95)
    for idx, r in enumerate(rows):
        title = r["document_title"] or r["document_id"]
        print(f"{idx:<4} | {r['regulator']:<5} | {r['total_chunks']:<7} | {r['total_relationships']:<9} | {title[:60]}")
    print("=" * 95 + f"\nTotal matching documents: {len(rows)}\n")
    return rows


def package_document_batch(
    document_id: str,
    batch_name: Optional[str] = None,
    chunk_slice_size: int = 50,
    output_dir: Optional[Path] = None,
) -> List[Path]:
    target_out_dir = Path(output_dir or DEFAULT_OUTPUT_DIR)
    target_out_dir.mkdir(parents=True, exist_ok=True)

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
    for row in chunk_rows:
        cid = row["chunk_id"]
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

    # Base naming
    doc_slug = doc_row["doc_stem"] or doc_row["document_id"].replace("::", "_").replace(" ", "_")
    base_name = batch_name or f"eval_batch_{doc_row['regulator']}_{doc_slug[:40]}"

    # Determine whether slicing is needed
    created_files: List[Path] = []
    total_chunks = len(chunks_data)

    if chunk_slice_size <= 0 or total_chunks <= chunk_slice_size:
        slices = [(1, 1, chunks_data)]
    else:
        num_slices = (total_chunks + chunk_slice_size - 1) // chunk_slice_size
        slices = [
            (
                part_idx + 1,
                num_slices,
                chunks_data[part_idx * chunk_slice_size : (part_idx + 1) * chunk_slice_size],
            )
            for part_idx in range(num_slices)
        ]

    for part_idx, num_parts, slice_chunks in slices:
        part_suffix = f"_part{part_idx}of{num_parts}" if num_parts > 1 else ""
        slice_batch_id = f"{base_name}{part_suffix}"
        out_file = target_out_dir / f"{slice_batch_id}.json"

        # Filter entities and relationships to those belonging to chunks in this slice
        slice_cids = {c["chunk_id"] for c in slice_chunks}
        slice_entities = [e for e in entities if e.get("chunk_id") in slice_cids]
        slice_relationships = [r for r in relationships if r.get("chunk_id") in slice_cids]
        slice_triplets_by_chunk = {cid: triplets_by_chunk[cid] for cid in slice_cids if cid in triplets_by_chunk}

        batch_payload = {
            "batch_id": slice_batch_id,
            "document_id": document_id,
            "document_title": doc_row["document_title"],
            "regulator": doc_row["regulator"],
            "part_index": part_idx,
            "total_parts": num_parts,
            "total_chunks": len(slice_chunks),
            "total_existing_entities": len(slice_entities),
            "total_existing_triplets": len(slice_relationships),
            "chunks": slice_chunks,
            "entities": slice_entities,
            "relationships": slice_relationships,
            "triplets_by_chunk": slice_triplets_by_chunk,
        }

        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(batch_payload, f, indent=2, ensure_ascii=False)

        size_kb = out_file.stat().st_size / 1024.0
        print(f"Packaged batch: {out_file.name} ({size_kb:.1f} KB) | Chunks: {len(slice_chunks)} | Triplets: {len(slice_relationships)}")
        created_files.append(out_file)

    return created_files


def main():
    parser = argparse.ArgumentParser(description="Package Knowledge Graph evaluation batches for LLM Refinement.")
    parser.add_argument("--list-docs", action="store_true", help="List candidate documents with chunk & triplet counts.")
    parser.add_argument("--regulator", type=str, default=None, help="Filter by regulator (MCA, RBI, SEBI).")
    parser.add_argument("--doc-idx", type=int, default=None, help="Index from list-docs to package.")
    parser.add_argument("--doc-id", type=str, default=None, help="Exact document_id to package.")
    parser.add_argument("--all", action="store_true", help="Package all matching documents into sliced batches.")
    parser.add_argument("--chunk-slice-size", type=int, default=50, help="Max chunks per batch file (default: 50).")
    parser.add_argument("--min-triplets", type=int, default=1, help="Minimum triplets required for inclusion (default: 1).")
    parser.add_argument("--batch-name", type=str, default=None, help="Custom filename prefix.")
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory for packaged batches.")

    args = parser.parse_args()

    out_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR

    if args.list_docs:
        list_candidate_documents(regulator=args.regulator, min_triplets=args.min_triplets)
        return

    if args.all:
        docs = list_candidate_documents(regulator=args.regulator, min_triplets=args.min_triplets, limit=None)
        print(f"\nPackaging ALL {len(docs)} documents into bounded batches (slice size: {args.chunk_slice_size})...")
        total_batches = 0
        for i, d in enumerate(docs, 1):
            print(f"[{i}/{len(docs)}] Packaging document: {d['document_title'][:60]}...")
            created = package_document_batch(
                document_id=d["document_id"],
                chunk_slice_size=args.chunk_slice_size,
                output_dir=out_dir
            )
            total_batches += len(created)
        print(f"\nSuccessfully generated {total_batches} batches in {out_dir}.\n")
        return

    if args.doc_idx is not None:
        docs = list_candidate_documents(regulator=args.regulator, min_triplets=args.min_triplets)
        if 0 <= args.doc_idx < len(docs):
            target_doc_id = docs[args.doc_idx]["document_id"]
            package_document_batch(
                document_id=target_doc_id,
                batch_name=args.batch_name,
                chunk_slice_size=args.chunk_slice_size,
                output_dir=out_dir
            )
        else:
            print(f"Error: Invalid doc-idx {args.doc_idx}. Must be 0 to {len(docs)-1}.")
        return

    if args.doc_id:
        package_document_batch(
            document_id=args.doc_id,
            batch_name=args.batch_name,
            chunk_slice_size=args.chunk_slice_size,
            output_dir=out_dir
        )
        return

    # Default action: list documents
    list_candidate_documents(regulator=args.regulator, min_triplets=args.min_triplets)


if __name__ == "__main__":
    main()
