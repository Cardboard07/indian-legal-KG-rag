"""FalkorDB / GraphRAG export engine.
Produces graph-ready JSONL files:
- entities.jsonl
- relationships.jsonl
- evidence.jsonl
- unresolved_references.jsonl
- manifest.json
"""

import os
import json
import shutil
from pathlib import Path
from typing import Dict, Any
from src.config import EXPORTS_DIR, MANIFEST_PATH
from src.db import ExtractionStore, get_db_connection


class GraphRAGExporter:
    """Exports structured extraction records to FalkorDB-ready JSONL files."""

    def __init__(self, store: ExtractionStore, export_dir: Path = EXPORTS_DIR):
        self.store = store
        self.export_dir = export_dir
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def export_all(self) -> Dict[str, int]:
        print(f"Exporting GraphRAG datasets to {self.export_dir}...")
        counts = {}

        # 1. Export entities.jsonl
        entities_path = self.export_dir / "entities.jsonl"
        with get_db_connection(self.store.db_path) as conn, open(entities_path, "w", encoding="utf-8") as f:
            cursor = conn.execute("SELECT * FROM entities;")
            e_count = 0
            for row in cursor:
                ev_data = json.loads(row["evidence_json"]) if row["evidence_json"] else []
                req_data = json.loads(row["requirement_details_json"]) if row["requirement_details_json"] else None
                rec = {
                    "entity_id": row["entity_id"],
                    "chunk_id": row["chunk_id"],
                    "document_id": row["document_id"],
                    "local_entity_id": row["local_entity_id"],
                    "mention": row["mention"],
                    "canonical_name": row["canonical_name"],
                    "entity_type": row["entity_type"],
                    "description": row["description"],
                    "identity_scope": row["identity_scope"],
                    "evidence": ev_data,
                    "requirement_details": req_data,
                    "review_status": row["review_status"],
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                e_count += 1
        counts["entities"] = e_count

        # 2. Export relationships.jsonl
        relationships_path = self.export_dir / "relationships.jsonl"
        with get_db_connection(self.store.db_path) as conn, open(relationships_path, "w", encoding="utf-8") as f:
            cursor = conn.execute("SELECT * FROM relationships;")
            r_count = 0
            for row in cursor:
                rec = {
                    "relationship_id": row["relationship_id"],
                    "chunk_id": row["chunk_id"],
                    "document_id": row["document_id"],
                    "local_relationship_id": row["local_relationship_id"],
                    "source_entity_id": row["source_entity_id"],
                    "target_entity_id": row["target_entity_id"],
                    "relationship_type": row["relationship_type"],
                    "description": row["description"],
                    "conditions": json.loads(row["conditions_json"]) if row["conditions_json"] else [],
                    "exceptions": json.loads(row["exceptions_json"]) if row["exceptions_json"] else [],
                    "numeric_constraints": json.loads(row["numeric_constraints_json"]) if row["numeric_constraints_json"] else [],
                    "temporal_qualifiers": json.loads(row["temporal_qualifiers_json"]) if row["temporal_qualifiers_json"] else [],
                    "evidence": json.loads(row["evidence_json"]) if row["evidence_json"] else [],
                    "review_status": row["review_status"],
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                r_count += 1
        counts["relationships"] = r_count

        # 3. Export evidence.jsonl
        evidence_path = self.export_dir / "evidence.jsonl"
        with get_db_connection(self.store.db_path) as conn, open(evidence_path, "w", encoding="utf-8") as f:
            cursor = conn.execute("SELECT * FROM evidence_spans;")
            ev_count = 0
            for row in cursor:
                rec = {
                    "id": row["id"],
                    "chunk_id": row["chunk_id"],
                    "exact_quote": row["exact_quote"],
                    "char_start": row["char_start"],
                    "char_end": row["char_end"],
                    "page_number": row["page_number"],
                    "provision_locator": row["provision_locator"],
                    "verified": bool(row["verified"]),
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                ev_count += 1
        counts["evidence"] = ev_count

        # 4. Export unresolved_references.jsonl
        unresolved_path = self.export_dir / "unresolved_references.jsonl"
        with get_db_connection(self.store.db_path) as conn, open(unresolved_path, "w", encoding="utf-8") as f:
            cursor = conn.execute("SELECT * FROM unresolved_references;")
            u_count = 0
            for row in cursor:
                rec = {
                    "ref_id": row["ref_id"],
                    "chunk_id": row["chunk_id"],
                    "document_id": row["document_id"],
                    "target_text": row["target_text"],
                    "ref_type": row["ref_type"],
                    "context_needed": row["context_needed"],
                    "status": row["status"],
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                u_count += 1
        counts["unresolved_references"] = u_count

        # 5. Export manifest.json
        manifest_export_path = self.export_dir / "manifest.json"
        if MANIFEST_PATH.exists():
            shutil.copy2(MANIFEST_PATH, manifest_export_path)
            counts["manifest"] = 1
        else:
            counts["manifest"] = 0

        print(f"Export completed: {counts}")
        return counts
