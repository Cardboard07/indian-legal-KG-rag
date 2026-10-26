"""Knowledge Graph Refinement Reconciliation & Ingestion Engine.
Processes cluster evaluation results:
1. Quarantines invalid / hallucinated relationships in extraction_store.db.
2. Validates verbatim quote grounding for newly discovered triplets.
3. Ingests verified missing entities, relationships, qualifiers, and evidence spans into the Knowledge Graph.

Usage:
    # 1. Preview reconciliation in dry-run mode (does not alter database)
    python scripts/reconcile_refinement_results.py --results data/eval_batches/results/refinement_results_eval_batch_RBI_...json --dry-run

    # 2. Apply reconciliation to SQLite database
    python scripts/reconcile_refinement_results.py --results data/eval_batches/results/refinement_results_eval_batch_RBI_...json --apply

    # 3. Process all result files in directory
    python scripts/reconcile_refinement_results.py --results-dir data/eval_batches/results/ --apply
"""

import os
import sys
import json
import sqlite3
import argparse
import hashlib
from contextlib import closing
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

# Base paths
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src.config import DB_PATH, ENTITY_TYPES, RELATIONSHIP_TYPES
from src.db import get_db_connection
from src.refinement_contract import validate_proposal


def init_audit_tables(conn: sqlite3.Connection):
    """Ensures audit tracking tables exist in SQLite."""
    conn.execute("""
    CREATE TABLE IF NOT EXISTS refinement_audits (
        audit_id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL,
        chunk_id TEXT NOT NULL,
        relationship_id TEXT NOT NULL,
        is_valid INTEGER NOT NULL,
        critique TEXT,
        error_type TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()


def locate_quote_offsets(raw_text: str, quote: str) -> Tuple[int, int]:
    """Finds exact or whitespace-normalized quote offsets in chunk text."""
    if not quote or not raw_text:
        return -1, -1

    # 1. Exact match
    idx = raw_text.find(quote)
    if idx != -1:
        return idx, idx + len(quote)

    # 2. Whitespace-normalized match across linebreaks
    import re
    norm_quote = re.sub(r"\s+", " ", quote).strip()
    words = norm_quote.split()
    if len(words) >= 2:
        first_w = re.escape(words[0])
        last_w = re.escape(words[-1])
        pattern = re.compile(rf"{first_w}\b.*?\b{last_w}", re.DOTALL)
        m = pattern.search(raw_text)
        if m:
            return m.start(), m.end()

    return -1, -1


def reconcile_result_file(
    results_path: Path,
    apply_changes: bool = False
) -> Dict[str, Any]:
    with open(results_path, "r", encoding="utf-8") as fp:
        results_data = json.load(fp)

    batch_id = results_data["batch_id"]
    doc_title = results_data.get("document_title", "Unknown")
    if results_data.get("execution_mode") != "live":
        raise ValueError("Only explicitly identified live results may be reconciled")
    chunk_evaluations = [item for item in results_data.get("results", []) if item.get("status") == "success"]

    print("\n" + "=" * 80)
    print(f"RECONCILING REFINEMENT RESULTS: {results_path.name}")
    print(f"Batch ID : {batch_id}")
    print(f"Document : {doc_title}")
    print(f"Mode     : {'APPLY TO DATABASE' if apply_changes else 'DRY RUN (Preview Only)'}")
    print("=" * 80)

    stats = {
        "chunks_evaluated": len(chunk_evaluations),
        "total_audited": 0,
        "valid_kept": 0,
        "repaired": 0,
        "quarantined": 0,
        "missing_suggested": 0,
        "missing_grounded": 0,
        "missing_ungrounded": 0,
        "entities_created": 0,
        "relationships_created": 0,
    }

    # Read-only preview must not create tables, journals or an empty database.
    connection = get_db_connection(DB_PATH) if apply_changes else closing(
        sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True))
    with connection as conn:
        conn.row_factory = sqlite3.Row
        if apply_changes:
            init_audit_tables(conn)

        # Cache document_id and raw text for chunks
        chunk_cache: Dict[str, Dict[str, Any]] = {}
        for item in chunk_evaluations:
            cid = item["chunk_id"]
            c_row = conn.execute(
                "SELECT chunk_id, document_id, file_path, section_locator FROM manifest_chunks WHERE chunk_id = ?",
                (cid,)
            ).fetchone()
            if c_row:
                fpath = Path(c_row["file_path"])
                if not fpath.is_absolute():
                    fpath = ROOT_DIR / fpath
                raw_text = ""
                if fpath.exists():
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as fp:
                            raw_text = fp.read().strip()
                    except Exception:
                        pass
                chunk_cache[cid] = {
                    "document_id": c_row["document_id"],
                    "locator": c_row["section_locator"],
                    "raw_text": raw_text
                }

        # 1. Process Audited Existing Triplets
        for item in chunk_evaluations:
            cid = item["chunk_id"]
            eval_data = item.get("evaluation", {})
            audited = eval_data.get("audited_triplets", [])

            for a in audited:
                stats["total_audited"] += 1
                rid = a.get("relationship_id")
                is_valid = a.get("is_valid")
                if not isinstance(rid, str) or (is_valid is not None and type(is_valid) is not bool):
                    raise ValueError("Invalid audit identity or boolean")
                if is_valid is None:
                    continue
                matched = conn.execute(
                    "SELECT relationship_id FROM relationships WHERE chunk_id=? AND (relationship_id=? OR local_relationship_id=?)",
                    (cid, rid, rid)).fetchone()
                if matched is None:
                    raise ValueError(f"Unknown relationship {rid} for chunk {cid}")
                critique = a.get("critique", "")
                err_type = a.get("error_type", "none")
                corr = a.get("corrected_triplet")

                # Resolve relationship in DB
                global_rel_id = matched["relationship_id"]

                if is_valid:
                    stats["valid_kept"] += 1
                elif corr and isinstance(corr, dict):
                    stats["repaired"] += 1
                    if apply_changes:
                        audit_id = f"{batch_id}::{global_rel_id}"
                        conn.execute("""
                        INSERT INTO refinement_audits (audit_id, batch_id, chunk_id, relationship_id, is_valid, critique, error_type)
                        VALUES (?, ?, ?, ?, 2, ?, ?)
                        ON CONFLICT(audit_id) DO UPDATE SET critique=excluded.critique, error_type=excluded.error_type;
                        """, (audit_id, batch_id, cid, global_rel_id, critique, err_type))

                        # Apply the repaired relationship directly
                        conn.execute("""
                        UPDATE relationships
                        SET relationship_type = ?,
                            description = ?,
                            conditions_json = ?,
                            exceptions_json = ?,
                            numeric_constraints_json = ?,
                            temporal_qualifiers_json = ?,
                            review_status = 'repaired'
                        WHERE relationship_id = ? OR (chunk_id = ? AND local_relationship_id = ?);
                        """, (
                            corr.get("predicate", "RELATION"),
                            corr.get("description", ""),
                            json.dumps(corr.get("conditions", []), ensure_ascii=False),
                            json.dumps(corr.get("exceptions", []), ensure_ascii=False),
                            json.dumps(corr.get("numeric_constraints", []), ensure_ascii=False),
                            json.dumps(corr.get("temporal_qualifiers", []), ensure_ascii=False),
                            global_rel_id, cid, rid
                        ))
                else:
                    stats["quarantined"] += 1
                    if apply_changes:
                        audit_id = f"{batch_id}::{global_rel_id}"
                        conn.execute("""
                        INSERT INTO refinement_audits (audit_id, batch_id, chunk_id, relationship_id, is_valid, critique, error_type)
                        VALUES (?, ?, ?, ?, 0, ?, ?)
                        ON CONFLICT(audit_id) DO UPDATE SET critique=excluded.critique, error_type=excluded.error_type;
                        """, (audit_id, batch_id, cid, global_rel_id, critique, err_type))

                        # Mark relationship as quarantined
                        conn.execute("""
                        UPDATE relationships
                        SET review_status = 'quarantined'
                        WHERE relationship_id = ? OR (chunk_id = ? AND local_relationship_id = ?);
                        """, (global_rel_id, cid, rid))

        # 2. Process Missing Discovered Triplets
        counter = 1
        for item in chunk_evaluations:
            cid = item["chunk_id"]
            c_info = chunk_cache.get(cid, {})
            raw_text = c_info.get("raw_text", "")
            doc_id = c_info.get("document_id", "DOC_UNKNOWN")
            locator = c_info.get("locator", "")

            eval_data = item.get("evaluation", {})
            missing = eval_data.get("missing_triplets", [])

            for m in missing:
                stats["missing_suggested"] += 1
                subj = m.get("subject", "").strip()
                s_type = m.get("subject_type", "EntityCategory")
                pred = m.get("predicate", "").strip().upper()
                obj = m.get("object", "").strip()
                o_type = m.get("object_type", "Requirement")
                quote = m.get("exact_quote", "").strip()
                desc = m.get("description", f"{subj} {pred} {obj}")

                if not subj or not obj or not pred:
                    continue

                try:
                    validate_proposal(m, raw_text)
                except (ValueError, TypeError):
                    stats["missing_ungrounded"] += 1
                    continue

                # Check quote grounding
                c_start, c_end = locate_quote_offsets(raw_text, quote)
                if c_start == -1:
                    stats["missing_ungrounded"] += 1
                    continue

                stats["missing_grounded"] += 1

                if apply_changes:
                    digest = hashlib.sha256(json.dumps(m, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
                    src_ent_id = f"{cid}::REF_{digest}_S"
                    tgt_ent_id = f"{cid}::REF_{digest}_O"
                    rel_id = f"{cid}::REF_{digest}"
                    if conn.execute("SELECT 1 FROM relationships WHERE relationship_id=?", (rel_id,)).fetchone():
                        continue
                    counter = digest

                    ev_list = [{
                        "source_chunk_id": cid,
                        "exact_quote": raw_text[c_start:c_end],
                        "char_start": c_start,
                        "char_end": c_end,
                        "provision_locator": locator
                    }]
                    ev_json = json.dumps(ev_list)

                    # Insert Subject Entity
                    conn.execute("""
                    INSERT INTO entities (
                        entity_id, chunk_id, document_id, local_entity_id, mention,
                        canonical_name, entity_type, description, identity_scope,
                        evidence_json, review_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'refined_discovery')
                    ON CONFLICT(entity_id) DO UPDATE SET description=excluded.description;
                    """, (
                        src_ent_id, cid, doc_id, f"REF_S{counter}", subj, subj,
                        s_type, f"Canonical entity: {subj}", f"Refined::{subj}", ev_json
                    ))
                    stats["entities_created"] += 1

                    # Insert Object Entity
                    conn.execute("""
                    INSERT INTO entities (
                        entity_id, chunk_id, document_id, local_entity_id, mention,
                        canonical_name, entity_type, description, identity_scope,
                        evidence_json, review_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'refined_discovery')
                    ON CONFLICT(entity_id) DO UPDATE SET description=excluded.description;
                    """, (
                        tgt_ent_id, cid, doc_id, f"REF_O{counter}", obj, obj,
                        o_type, f"Canonical entity: {obj}", f"Refined::{obj}", ev_json
                    ))
                    stats["entities_created"] += 1

                    # Insert Relationship
                    cond_json = json.dumps(m.get("conditions", []))
                    exc_json = json.dumps(m.get("exceptions", []))
                    num_json = json.dumps(m.get("numeric_constraints", []))
                    temp_json = json.dumps(m.get("temporal_qualifiers", []))

                    conn.execute("""
                    INSERT INTO relationships (
                        relationship_id, chunk_id, document_id, local_relationship_id,
                        source_entity_id, target_entity_id, relationship_type, description,
                        conditions_json, exceptions_json, numeric_constraints_json,
                        temporal_qualifiers_json, evidence_json, review_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'refined_discovery')
                    ON CONFLICT(relationship_id) DO UPDATE SET review_status='refined_discovery';
                    """, (
                        rel_id, cid, doc_id, f"REF_R{counter}",
                        src_ent_id, tgt_ent_id, pred, desc,
                        cond_json, exc_json, num_json, temp_json, ev_json
                    ))
                    stats["relationships_created"] += 1

                    # Record in evidence_spans table
                    conn.execute("""
                    INSERT INTO evidence_spans (
                        chunk_id, exact_quote, char_start, char_end, provision_locator, verified
                    ) VALUES (?, ?, ?, ?, ?, 1);
                    """, (cid, quote, c_start, c_end, locator))

        if apply_changes:
            conn.commit()

    print("\n" + "-" * 40 + " SUMMARY " + "-" * 40)
    print(f"Chunks Evaluated                : {stats['chunks_evaluated']}")
    print(f"Existing Triplets Audited       : {stats['total_audited']}")
    print(f"  - Valid & Kept                : {stats['valid_kept']}")
    print(f"  - Repaired with Grounding     : {stats['repaired']}")
    print(f"  - Quarantined (False/Distorted): {stats['quarantined']}")
    print(f"Missing Triplets Suggested      : {stats['missing_suggested']}")
    print(f"  - Verbatim Grounded (Verified): {stats['missing_grounded']}")
    print(f"  - Ungrounded / Hallucinated   : {stats['missing_ungrounded']} (Rejected)")
    if apply_changes:
        print(f"New Entities Ingested           : {stats['entities_created']}")
        print(f"New Relationships Ingested      : {stats['relationships_created']}")
        print(f"Database Updated                : {DB_PATH}")
    else:
        print(f"Status                          : DRY RUN (0 changes written to database)")
    print("-" * 89 + "\n")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Reconcile Knowledge Graph Refinement results into SQLite database.")
    parser.add_argument("--results", type=str, default=None, help="Path to single refinement_results_<batch>.json file.")
    parser.add_argument("--results-dir", type=str, default=None, help="Directory containing refinement result JSON files.")
    parser.add_argument("--apply", action="store_true", help="Apply changes (quarantine & ingestion) to database.")
    parser.add_argument("--dry-run", action="store_true", help="Preview reconciliation without modifying database.")

    args = parser.parse_args()

    if not args.apply and not args.dry_run:
        print("Note: Running in --dry-run mode by default. Specify --apply to write changes to database.")
        args.dry_run = True

    apply_changes = args.apply and not args.dry_run

    if args.results:
        p = Path(args.results)
        if not p.exists():
            print(f"Error: Results file not found at {p}")
            return
        reconcile_result_file(p, apply_changes=apply_changes)
        return

    if args.results_dir:
        d = Path(args.results_dir)
        files = list(d.glob("refinement_results_*.json"))
        if not files:
            print(f"No refinement_results_*.json files found in {d}")
            return
        print(f"Found {len(files)} result files in {d}. Processing...")
        for f in files:
            reconcile_result_file(f, apply_changes=apply_changes)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
