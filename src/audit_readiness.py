"""Extraction readiness and quarantine auditor.
Validates extraction records before loading into FalkorDB.
Distinguishes:
- structurally valid extracted candidates (unreviewed dev mode)
- human-reviewed records
- unresolved reference records
- quarantined invalid records
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, Any, List, Set, Tuple

from src.config import (
    DB_PATH,
    EXPORTS_DIR,
    ENTITY_TYPES,
    RELATIONSHIP_TYPES,
    PERMITTED_RELATIONSHIP_ENDPOINTS,
    DATA_DIR,
)
from src.db import get_db_connection


class ExtractionAuditor:
    """Audits extraction store records for FalkorDB ingestion readiness."""

    def __init__(self, db_path: Path = DB_PATH, exports_dir: Path = EXPORTS_DIR):
        self.db_path = db_path
        self.exports_dir = exports_dir
        self.quarantine_dir = DATA_DIR / "quarantine"
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

    def run_audit(self) -> Dict[str, Any]:
        print("=== Starting Comprehensive Extraction Readiness Audit ===", flush=True)
        t0 = time.time()

        quarantined_entities = []
        quarantined_relationships = []
        quarantined_evidence = []

        valid_entities_count = 0
        valid_relationships_count = 0
        valid_evidence_count = 0
        human_reviewed_count = 0
        unreviewed_dev_count = 0

        with get_db_connection(self.db_path) as conn:
            # 1. Audit Documents and Chunks
            doc_ids: Set[str] = {r[0] for r in conn.execute("SELECT document_id FROM documents;").fetchall()}
            chunk_ids: Set[str] = {r[0] for r in conn.execute("SELECT chunk_id FROM manifest_chunks;").fetchall()}
            print(f"Loaded {len(doc_ids)} documents and {len(chunk_ids)} manifest chunks for referential checks.", flush=True)

            # 2. Audit Entities
            print("Auditing entities table...", flush=True)
            cursor = conn.execute("""
                SELECT entity_id, chunk_id, document_id, local_entity_id, mention,
                       canonical_name, entity_type, description, identity_scope,
                       evidence_json, requirement_details_json, review_status
                FROM entities;
            """)

            entity_id_map: Dict[str, str] = {}  # entity_id -> entity_type

            for row in cursor:
                eid = row["entity_id"]
                cid = row["chunk_id"]
                did = row["document_id"]
                etype = row["entity_type"]
                mention = row["mention"]
                cname = row["canonical_name"]
                rstatus = row["review_status"]

                reasons = []
                if not eid:
                    reasons.append("Missing entity_id")
                if eid in entity_id_map:
                    reasons.append(f"Duplicate entity_id: {eid}")
                if cid not in chunk_ids:
                    reasons.append(f"Referenced chunk_id not in manifest: {cid}")
                if did not in doc_ids:
                    reasons.append(f"Referenced document_id not in documents: {did}")
                if etype not in ENTITY_TYPES:
                    reasons.append(f"Invalid entity_type: {etype}")
                if not mention or not mention.strip():
                    reasons.append("Empty entity mention")
                if not cname or not cname.strip():
                    reasons.append("Empty canonical_name")

                # Verify JSON fields
                if row["evidence_json"]:
                    try:
                        json.loads(row["evidence_json"])
                    except Exception as e:
                        reasons.append(f"Corrupt evidence_json: {e}")

                if row["requirement_details_json"]:
                    try:
                        json.loads(row["requirement_details_json"])
                    except Exception as e:
                        reasons.append(f"Corrupt requirement_details_json: {e}")

                if rstatus == "approved":
                    human_reviewed_count += 1
                else:
                    unreviewed_dev_count += 1

                if reasons:
                    quarantined_entities.append({
                        "entity_id": eid,
                        "chunk_id": cid,
                        "reasons": reasons,
                    })
                else:
                    entity_id_map[eid] = etype
                    valid_entities_count += 1

            print(f"Entities audited: {valid_entities_count} valid, {len(quarantined_entities)} quarantined.", flush=True)

            # 3. Audit Relationships
            print("Auditing relationships table...", flush=True)
            r_cursor = conn.execute("""
                SELECT relationship_id, chunk_id, document_id, local_relationship_id,
                       source_entity_id, target_entity_id, relationship_type, description,
                       conditions_json, exceptions_json, numeric_constraints_json,
                       temporal_qualifiers_json, evidence_json, review_status
                FROM relationships;
            """)

            rel_ids: Set[str] = set()

            for row in r_cursor:
                rid = row["relationship_id"]
                cid = row["chunk_id"]
                did = row["document_id"]
                s_id = row["source_entity_id"]
                t_id = row["target_entity_id"]
                rtype = row["relationship_type"]

                reasons = []
                if not rid:
                    reasons.append("Missing relationship_id")
                if rid in rel_ids:
                    reasons.append(f"Duplicate relationship_id: {rid}")
                rel_ids.add(rid)

                if cid not in chunk_ids:
                    reasons.append(f"Referenced chunk_id not in manifest: {cid}")
                if did not in doc_ids:
                    reasons.append(f"Referenced document_id not in documents: {did}")

                s_type = entity_id_map.get(s_id)
                t_type = entity_id_map.get(t_id)

                if not s_type:
                    reasons.append(f"Dangling source_entity_id: {s_id}")
                if not t_type:
                    reasons.append(f"Dangling target_entity_id: {t_id}")

                if rtype not in RELATIONSHIP_TYPES:
                    reasons.append(f"Invalid relationship_type: {rtype}")
                elif s_type and t_type:
                    permitted = PERMITTED_RELATIONSHIP_ENDPOINTS.get(rtype, {})
                    valid_sources = permitted.get("sources", set())
                    valid_targets = permitted.get("targets", set())
                    if s_type not in valid_sources:
                        reasons.append(f"Invalid source type {s_type} for relationship {rtype}")
                    if t_type not in valid_targets:
                        reasons.append(f"Invalid target type {t_type} for relationship {rtype}")

                # Check JSON fields
                for fld in ["conditions_json", "exceptions_json", "numeric_constraints_json", "temporal_qualifiers_json", "evidence_json"]:
                    if row[fld]:
                        try:
                            json.loads(row[fld])
                        except Exception as e:
                            reasons.append(f"Corrupt {fld}: {e}")

                if reasons:
                    quarantined_relationships.append({
                        "relationship_id": rid,
                        "chunk_id": cid,
                        "source_id": s_id,
                        "target_id": t_id,
                        "reasons": reasons,
                    })
                else:
                    valid_relationships_count += 1

            print(f"Relationships audited: {valid_relationships_count} valid, {len(quarantined_relationships)} quarantined.", flush=True)

            # 4. Audit Evidence Spans
            print("Auditing evidence spans...", flush=True)
            ev_cursor = conn.execute("""
                SELECT id, chunk_id, exact_quote, char_start, char_end, verified
                FROM evidence_spans;
            """)

            for row in ev_cursor:
                ev_id = row["id"]
                cid = row["chunk_id"]
                quote = row["exact_quote"]
                c_start = row["char_start"]
                c_end = row["char_end"]

                reasons = []
                if cid not in chunk_ids:
                    reasons.append(f"Evidence chunk_id not in manifest: {cid}")
                if not quote or not quote.strip():
                    reasons.append("Empty evidence quote")
                if c_start is not None and c_end is not None and c_start >= 0 and c_end >= 0:
                    if c_end <= c_start:
                        reasons.append(f"Invalid offsets: start {c_start} >= end {c_end}")

                if reasons:
                    quarantined_evidence.append({
                        "evidence_id": ev_id,
                        "chunk_id": cid,
                        "reasons": reasons,
                    })
                else:
                    valid_evidence_count += 1

            print(f"Evidence spans audited: {valid_evidence_count} valid, {len(quarantined_evidence)} quarantined.", flush=True)

            # 5. Audit Unresolved References
            unresolved_count = conn.execute("SELECT COUNT(*) FROM unresolved_references;").fetchone()[0]

            # 6. Audit Entity Resolution Decisions
            canonical_cluster_count = conn.execute("SELECT COUNT(*) FROM entity_resolutions;").fetchone()[0]

        # Save Quarantine Report
        report = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "audit_duration_seconds": round(time.time() - t0, 2),
            "execution_mode": "unreviewed_extraction_dev_mode",
            "summary": {
                "total_documents": len(doc_ids),
                "total_manifest_chunks": len(chunk_ids),
                "valid_entities": valid_entities_count,
                "quarantined_entities": len(quarantined_entities),
                "valid_relationships": valid_relationships_count,
                "quarantined_relationships": len(quarantined_relationships),
                "valid_evidence_spans": valid_evidence_count,
                "quarantined_evidence_spans": len(quarantined_evidence),
                "unresolved_references": unresolved_count,
                "canonical_resolved_clusters": canonical_cluster_count,
                "human_reviewed_records": human_reviewed_count,
                "unreviewed_candidate_records": unreviewed_dev_count,
            },
            "quarantined_samples": {
                "entities": quarantined_entities[:20],
                "relationships": quarantined_relationships[:20],
                "evidence": quarantined_evidence[:20],
            }
        }

        report_path = self.quarantine_dir / "audit_quarantine_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        print(f"Audit completed in {report['audit_duration_seconds']}s.")
        print(f"Report saved to: {report_path}")
        return report


if __name__ == "__main__":
    auditor = ExtractionAuditor()
    rep = auditor.run_audit()
    print("\n=== AUDIT SUMMARY ===")
    for k, v in rep["summary"].items():
        print(f"  {k}: {v}")
