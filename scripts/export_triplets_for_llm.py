"""Export Knowledge Graph Triplets into LLM-Ready Formats (Markdown, Plain Text, JSONL).
Enables zero-friction consumption by language models for RAG, fine-tuning, or evaluation.
"""

import sqlite3
import json
import argparse
from pathlib import Path

DB_PATH = Path("data/extraction_store.db")

def export_triplets(
    output_format: str = "markdown",
    output_file: str = "triplets_for_llm.md",
    regulator: str = None,
    relationship_types: list = None,
    limit: int = 100
):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    query = """
    SELECT 
        e_src.canonical_name AS subject,
        e_src.entity_type AS subject_type,
        r.relationship_type AS predicate,
        e_tgt.canonical_name AS object,
        e_tgt.entity_type AS object_type,
        r.description,
        r.conditions_json,
        r.exceptions_json,
        r.numeric_constraints_json,
        r.chunk_id
    FROM relationships r
    JOIN entities e_src ON r.source_entity_id = e_src.entity_id
    JOIN entities e_tgt ON r.target_entity_id = e_tgt.entity_id
    """
    conditions = []
    params = []

    if regulator:
        conditions.append("r.chunk_id LIKE ?")
        params.append(f"{regulator}/%")

    if relationship_types:
        placeholders = ",".join(["?"] * len(relationship_types))
        conditions.append(f"r.relationship_type IN ({placeholders})")
        params.extend(relationship_types)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " LIMIT ?"
    params.append(limit)

    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()
    conn.close()

    print(f"Retrieved {len(rows)} triplets from database.")

    with open(output_file, "w", encoding="utf-8") as f:
        if output_format == "markdown":
            f.write("# Knowledge Graph Triplet Table for LLM Context\n\n")
            f.write("| Subject | Predicate | Object | Rule / Description | Conditions & Exceptions |\n")
            f.write("| :--- | :--- | :--- | :--- | :--- |\n")
            for r in rows:
                subj, s_type, pred, obj, o_type, desc, conds, excs, nums, chunk = r
                quals = []
                if conds and conds != "[]":
                    c_list = json.loads(conds)
                    quals.extend([f"If: {c.get('description', c)}" if isinstance(c, dict) else f"If: {c}" for c in c_list])
                if excs and excs != "[]":
                    e_list = json.loads(excs)
                    quals.extend([f"Except: {e}" for e in e_list])
                if nums and nums != "[]":
                    n_list = json.loads(nums)
                    quals.extend([f"Threshold: {n.get('context_text', n)}" if isinstance(n, dict) else f"Threshold: {n}" for n in n_list])

                qual_str = "; ".join(quals) if quals else "None"
                # Sanitize pipes
                clean_desc = (desc or "").replace("|", "-").replace("\n", " ")
                clean_subj = subj.replace("|", "-")
                clean_obj = obj.replace("|", "-")
                f.write(f"| **{clean_subj}** (`{s_type}`) | `{pred}` | **{clean_obj}** (`{o_type}`) | {clean_desc} | {qual_str} |\n")

        elif output_format == "text":
            f.write("# Knowledge Graph Semantic Triplet Statements\n\n")
            for r in rows:
                subj, s_type, pred, obj, o_type, desc, conds, excs, nums, chunk = r
                f.write(f"- [{subj}] --{pred}--> [{obj}]\n")
                if desc:
                    f.write(f"  Description: {desc}\n")
                if excs and excs != "[]":
                    f.write(f"  Exceptions: {excs}\n")
                if nums and nums != "[]":
                    f.write(f"  Thresholds: {nums}\n")
                f.write(f"  Source Chunk: {chunk}\n\n")

        elif output_format == "jsonl":
            for r in rows:
                subj, s_type, pred, obj, o_type, desc, conds, excs, nums, chunk = r
                record = {
                    "subject": subj,
                    "subject_type": s_type,
                    "predicate": pred,
                    "object": obj,
                    "object_type": o_type,
                    "description": desc,
                    "conditions": json.loads(conds) if conds else [],
                    "exceptions": json.loads(excs) if excs else [],
                    "numeric_constraints": json.loads(nums) if nums else [],
                    "chunk_id": chunk
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Successfully saved {len(rows)} triplets to '{output_file}' in '{output_format}' format.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export KG triplets in LLM-ready format.")
    parser.add_argument("--format", choices=["markdown", "text", "jsonl"], default="markdown", help="Output format")
    parser.add_argument("--output", default="triplets_for_llm.md", help="Output file path")
    parser.add_argument("--regulator", choices=["RBI", "MCA", "SEBI"], default=None, help="Filter by regulator")
    parser.add_argument("--limit", type=int, default=100, help="Number of triplets to export")
    args = parser.parse_args()

    export_triplets(
        output_format=args.format,
        output_file=args.output,
        regulator=args.regulator,
        limit=args.limit
    )
