"""Knowledge Graph Refinement Reconciliation & Ingestion Engine.
Processes cluster evaluation results:
1. Safeguard 1: Creates full physical database backup using SQLite Backup API (with WAL checkpoint).
2. Safeguard 2: Creates immutable _pre_refinement snapshots of key tables, validates row counts, and installs ABORT triggers.
3. Safeguard 3: Logs complete audit trail for EVERY verdict (valid, repaired, quarantined, needs_context, discovery)
   with before/after states, error types, critiques, and execution provenance.
4. Quarantines invalid / hallucinated relationships in extraction_store.db.
5. Ingests repaired and newly discovered grounded relationships into extraction_store.db.

Usage:
    # 1. Preview reconciliation in dry-run mode (does not alter database)
    python scripts/reconcile_refinement_results.py --results-dir data/eval_batches/results/ --dry-run

    # 2. Apply reconciliation with full backups and immutability safeguards
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


def create_database_backup(src_path: Path, dst_path: Optional[Path] = None) -> Path:
    """Safeguard 1: Safely creates a byte-consistent physical backup of SQLite DB using SQLite Backup API.
    Flushes all uncheckpointed WAL pages first.
    """
    if dst_path is None:
        dst_path = src_path.parent / f"{src_path.stem}_pre_refinement{src_path.suffix}"

    if dst_path.exists():
        print(f"[SAFEGUARD 1] Pre-refinement backup file already exists at {dst_path} "
              f"({dst_path.stat().st_size / (1024**3):.2f} GB). Preserving existing backup.")
        return dst_path

    print(f"\n[SAFEGUARD 1] Creating verified SQLite online backup: {src_path.name} -> {dst_path.name}...")
    src_conn = sqlite3.connect(src_path)
    dst_conn = sqlite3.connect(dst_path)
    try:
        # Checkpoint WAL pages into main database file
        src_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    if not dst_path.exists() or dst_path.stat().st_size == 0:
        raise RuntimeError(f"Failed to create verified database backup at {dst_path}")

    print(f"[SAFEGUARD 1] Backup created successfully: {dst_path.name} "
          f"({dst_path.stat().st_size / (1024**3):.2f} GB)\n")
    return dst_path


def create_immutable_snapshots(conn: sqlite3.Connection) -> None:
    """Safeguard 2: Creates immutable _pre_refinement snapshots of key tables, validates row counts,
    and installs SQLite triggers to strictly abort on UPDATE, DELETE, or INSERT.
    """
    tables_to_snapshot = ["relationships", "entities", "evidence_spans"]
    print("[SAFEGUARD 2] Verifying / Creating immutable pre-refinement snapshot tables...")

    for tbl in tables_to_snapshot:
        snap_tbl = f"{tbl}_pre_refinement"

        # 1. Create table snapshot if not exists
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (snap_tbl,)
        ).fetchone()

        if not table_exists:
            print(f"  -> Creating snapshot table: {snap_tbl} AS SELECT * FROM {tbl}...")
            conn.execute(f"CREATE TABLE {snap_tbl} AS SELECT * FROM {tbl};")
            conn.commit()

        # 2. Verify row counts
        orig_cnt = conn.execute(f"SELECT count(*) FROM {tbl}").fetchone()[0]
        snap_cnt = conn.execute(f"SELECT count(*) FROM {snap_tbl}").fetchone()[0]
        print(f"  -> {snap_tbl}: {snap_cnt:,} rows (matches source {tbl}: {orig_cnt:,})")
        if orig_cnt != snap_cnt:
            raise ValueError(
                f"Snapshot row count mismatch for {snap_tbl}: snapshot has {snap_cnt} rows, "
                f"original has {orig_cnt} rows!"
            )

        # 3. Install immutability triggers to prevent ANY subsequent modification
        conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS prevent_update_{snap_tbl}
        BEFORE UPDATE ON {snap_tbl}
        BEGIN
            SELECT RAISE(ABORT, '{snap_tbl} is an immutable pre-refinement snapshot and cannot be modified');
        END;
        """)
        conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS prevent_delete_{snap_tbl}
        BEFORE DELETE ON {snap_tbl}
        BEGIN
            SELECT RAISE(ABORT, '{snap_tbl} is an immutable pre-refinement snapshot and cannot be deleted');
        END;
        """)
        conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS prevent_insert_{snap_tbl}
        BEFORE INSERT ON {snap_tbl}
        BEGIN
            SELECT RAISE(ABORT, '{snap_tbl} is an immutable pre-refinement snapshot and cannot accept new records');
        END;
        """)

    conn.commit()
    print("[SAFEGUARD 2] All snapshot tables verified and locked with triggers.\n")


def init_audit_tables(conn: sqlite3.Connection):
    """Safeguard 3: Ensures audit tracking tables exist with full before/after and provenance schema."""
    conn.execute("""
    CREATE TABLE IF NOT EXISTS refinement_audits (
        audit_id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL,
        chunk_id TEXT NOT NULL,
        relationship_id TEXT NOT NULL,
        verdict TEXT NOT NULL,
        is_valid INTEGER,
        critique TEXT,
        error_type TEXT,
        before_state_json TEXT,
        after_state_json TEXT,
        provenance_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Ensure any missing columns exist if table was initialized previously
    existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(refinement_audits)").fetchall()}
    cols_to_add = {
        "verdict": "TEXT",
        "before_state_json": "TEXT",
        "after_state_json": "TEXT",
        "provenance_json": "TEXT",
    }
    for col_name, col_type in cols_to_add.items():
        if col_name not in existing_cols:
            conn.execute(f"ALTER TABLE refinement_audits ADD COLUMN {col_name} {col_type};")

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
        "needs_context": 0,
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

                matched = conn.execute(
                    """
                    SELECT relationship_id, relationship_type, description, conditions_json, exceptions_json,
                           numeric_constraints_json, temporal_qualifiers_json, review_status
                    FROM relationships
                    WHERE chunk_id=? AND (relationship_id=? OR local_relationship_id=?)
                    """,
                    (cid, rid, rid)
                ).fetchone()

                if matched is None:
                    raise ValueError(f"Unknown relationship {rid} for chunk {cid}")

                global_rel_id = matched["relationship_id"]
                critique = a.get("critique", "")
                err_type = a.get("error_type", "none")
                corr = a.get("corrected_triplet")

                # Build snapshot of pre-refinement state
                before_state = {
                    "relationship_id": global_rel_id,
                    "relationship_type": matched["relationship_type"],
                    "description": matched["description"] or "",
                    "conditions": json.loads(matched["conditions_json"] or "[]"),
                    "exceptions": json.loads(matched["exceptions_json"] or "[]"),
                    "numeric_constraints": json.loads(matched["numeric_constraints_json"] or "[]"),
                    "temporal_qualifiers": json.loads(matched["temporal_qualifiers_json"] or "[]"),
                    "review_status": matched["review_status"] or "unreviewed",
                }
                before_json = json.dumps(before_state, ensure_ascii=False)

                provenance = {
                    "batch_id": batch_id,
                    "chunk_id": cid,
                    "local_relationship_id": rid,
                    "document_title": doc_title,
                    "execution_mode": results_data.get("execution_mode", "live"),
                    "model": results_data.get("model", "qwen2.5-32b-instruct"),
                }
                prov_json = json.dumps(provenance, ensure_ascii=False)

                if is_valid is True:
                    verdict = "valid"
                    is_valid_code = 1
                    after_state = {"review_status": "verified"}
                    after_json = json.dumps(after_state, ensure_ascii=False)
                    stats["valid_kept"] += 1

                    if apply_changes:
                        audit_id = f"{batch_id}::{global_rel_id}"
                        conn.execute("""
                        INSERT INTO refinement_audits (
                            audit_id, batch_id, chunk_id, relationship_id, verdict, is_valid, critique, error_type,
                            before_state_json, after_state_json, provenance_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(audit_id) DO UPDATE SET
                            verdict=excluded.verdict, is_valid=excluded.is_valid, critique=excluded.critique,
                            error_type=excluded.error_type, before_state_json=excluded.before_state_json,
                            after_state_json=excluded.after_state_json, provenance_json=excluded.provenance_json;
                        """, (audit_id, batch_id, cid, global_rel_id, verdict, is_valid_code, critique, err_type,
                              before_json, after_json, prov_json))

                        conn.execute("""
                        UPDATE relationships
                        SET review_status = 'verified'
                        WHERE relationship_id = ? OR (chunk_id = ? AND local_relationship_id = ?);
                        """, (global_rel_id, cid, rid))

                elif corr and isinstance(corr, dict):
                    verdict = "repaired"
                    is_valid_code = 2
                    after_state = {
                        "relationship_id": global_rel_id,
                        "relationship_type": corr.get("predicate", "RELATION"),
                        "description": corr.get("description", ""),
                        "conditions": corr.get("conditions", []),
                        "exceptions": corr.get("exceptions", []),
                        "numeric_constraints": corr.get("numeric_constraints", []),
                        "temporal_qualifiers": corr.get("temporal_qualifiers", []),
                        "review_status": "repaired",
                    }
                    after_json = json.dumps(after_state, ensure_ascii=False)
                    stats["repaired"] += 1

                    if apply_changes:
                        audit_id = f"{batch_id}::{global_rel_id}"
                        conn.execute("""
                        INSERT INTO refinement_audits (
                            audit_id, batch_id, chunk_id, relationship_id, verdict, is_valid, critique, error_type,
                            before_state_json, after_state_json, provenance_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(audit_id) DO UPDATE SET
                            verdict=excluded.verdict, is_valid=excluded.is_valid, critique=excluded.critique,
                            error_type=excluded.error_type, before_state_json=excluded.before_state_json,
                            after_state_json=excluded.after_state_json, provenance_json=excluded.provenance_json;
                        """, (audit_id, batch_id, cid, global_rel_id, verdict, is_valid_code, critique, err_type,
                              before_json, after_json, prov_json))

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

                elif is_valid is False:
                    verdict = "quarantined"
                    is_valid_code = 0
                    after_state = {"review_status": "quarantined"}
                    after_json = json.dumps(after_state, ensure_ascii=False)
                    stats["quarantined"] += 1

                    if apply_changes:
                        audit_id = f"{batch_id}::{global_rel_id}"
                        conn.execute("""
                        INSERT INTO refinement_audits (
                            audit_id, batch_id, chunk_id, relationship_id, verdict, is_valid, critique, error_type,
                            before_state_json, after_state_json, provenance_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(audit_id) DO UPDATE SET
                            verdict=excluded.verdict, is_valid=excluded.is_valid, critique=excluded.critique,
                            error_type=excluded.error_type, before_state_json=excluded.before_state_json,
                            after_state_json=excluded.after_state_json, provenance_json=excluded.provenance_json;
                        """, (audit_id, batch_id, cid, global_rel_id, verdict, is_valid_code, critique, err_type,
                              before_json, after_json, prov_json))

                        # Mark relationship as quarantined
                        conn.execute("""
                        UPDATE relationships
                        SET review_status = 'quarantined'
                        WHERE relationship_id = ? OR (chunk_id = ? AND local_relationship_id = ?);
                        """, (global_rel_id, cid, rid))

                else:  # is_valid is None (Needs Context)
                    verdict = "needs_context"
                    is_valid_code = None
                    after_state = {"review_status": "needs_context"}
                    after_json = json.dumps(after_state, ensure_ascii=False)
                    stats["needs_context"] += 1

                    if apply_changes:
                        audit_id = f"{batch_id}::{global_rel_id}"
                        conn.execute("""
                        INSERT INTO refinement_audits (
                            audit_id, batch_id, chunk_id, relationship_id, verdict, is_valid, critique, error_type,
                            before_state_json, after_state_json, provenance_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(audit_id) DO UPDATE SET
                            verdict=excluded.verdict, is_valid=excluded.is_valid, critique=excluded.critique,
                            error_type=excluded.error_type, before_state_json=excluded.before_state_json,
                            after_state_json=excluded.after_state_json, provenance_json=excluded.provenance_json;
                        """, (audit_id, batch_id, cid, global_rel_id, verdict, is_valid_code, critique, err_type,
                              before_json, after_json, prov_json))

        # 2. Process Missing Discovered Triplets
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

                    # Log newly discovered relationship in refinement_audits
                    audit_id = f"{batch_id}::{rel_id}"
                    after_state = {
                        "relationship_id": rel_id,
                        "relationship_type": pred,
                        "subject": subj,
                        "object": obj,
                        "description": desc,
                        "review_status": "refined_discovery"
                    }
                    provenance = {
                        "batch_id": batch_id,
                        "chunk_id": cid,
                        "discovery_quote": quote,
                        "execution_mode": results_data.get("execution_mode", "live"),
                        "model": results_data.get("model", "qwen2.5-32b-instruct"),
                    }
                    conn.execute("""
                    INSERT INTO refinement_audits (
                        audit_id, batch_id, chunk_id, relationship_id, verdict, is_valid, critique, error_type,
                        before_state_json, after_state_json, provenance_json
                    ) VALUES (?, ?, ?, ?, 'refined_discovery', 1, ?, 'none', NULL, ?, ?)
                    ON CONFLICT(audit_id) DO NOTHING;
                    """, (audit_id, batch_id, cid, rel_id, f"Verbatim grounded missing relationship from: {quote[:80]}",
                          json.dumps(after_state, ensure_ascii=False), json.dumps(provenance, ensure_ascii=False)))

        if apply_changes:
            conn.commit()

    print("\n" + "-" * 40 + " SUMMARY " + "-" * 40)
    print(f"Chunks Evaluated                : {stats['chunks_evaluated']}")
    print(f"Existing Triplets Audited       : {stats['total_audited']}")
    print(f"  - Valid & Kept                : {stats['valid_kept']}")
    print(f"  - Repaired with Grounding     : {stats['repaired']}")
    print(f"  - Quarantined (False/Distorted): {stats['quarantined']}")
    if stats.get("needs_context"):
        print(f"  - Needs Context (Pending)     : {stats['needs_context']}")
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
    parser.add_argument("--backup-path", type=str, default=None,
                        help="Custom path for pre-refinement database backup (default: data/extraction_store_pre_refinement.db).")

    args = parser.parse_args()

    if not args.apply and not args.dry_run:
        print("Note: Running in --dry-run mode by default. Specify --apply to write changes to database.")
        args.dry_run = True

    apply_changes = args.apply and not args.dry_run

    if apply_changes:
        # Safeguard 1: Create verified SQLite online backup
        backup_path = Path(args.backup_path) if args.backup_path else (DB_PATH.parent / f"{DB_PATH.stem}_pre_refinement{DB_PATH.suffix}")
        create_database_backup(DB_PATH, backup_path)

        # Safeguard 2: Create immutable pre-refinement snapshots and triggers
        with get_db_connection(DB_PATH) as conn:
            create_immutable_snapshots(conn)

    if args.results:
        p = Path(args.results)
        if not p.exists():
            print(f"Error: Results file not found at {p}")
            return
        reconcile_result_file(p, apply_changes=apply_changes)
        return

    if args.results_dir:
        d = Path(args.results_dir)
        files = sorted(list(d.glob("refinement_results_*.json")))
        if not files:
            print(f"No refinement_results_*.json files found in {d}")
            return
        print(f"Found {len(files)} result files in {d}. Processing...")
        total_stats = {
            "chunks_evaluated": 0,
            "total_audited": 0,
            "valid_kept": 0,
            "repaired": 0,
            "quarantined": 0,
            "needs_context": 0,
            "missing_suggested": 0,
            "missing_grounded": 0,
            "missing_ungrounded": 0,
            "entities_created": 0,
            "relationships_created": 0,
        }
        for f in files:
            stats = reconcile_result_file(f, apply_changes=apply_changes)
            for k, v in stats.items():
                total_stats[k] = total_stats.get(k, 0) + v

        print("\n" + "=" * 80)
        print("OVERALL RECONCILIATION SUMMARY ACROSS ALL BATCHES:")
        for k, v in total_stats.items():
            print(f"  {k:30s}: {v}")
        print("=" * 80 + "\n")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
