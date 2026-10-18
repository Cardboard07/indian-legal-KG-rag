"""Durable SQLite storage engine for regulatory knowledge graph extractions.
Provides atomic transactions, normalized relational tables, schema integrity,
cache keys, and separate processing/review statuses.
"""

import sqlite3
import json
import hashlib
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path
from src.config import (
    DB_PATH,
    SCHEMA_VERSION,
    PROMPT_VERSION,
    MODEL_NAME,
    EXECUTION_ROUTE,
)
from src.schema import ChunkExtractionOutput, EntityMention, RelationshipAssertion, UnresolvedReference


from contextlib import contextmanager


@contextmanager
def get_db_connection(db_path: Path = DB_PATH):
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def compute_cache_key(
    chunk_content: str,
    supplied_context: str = "",
    prompt_version: str = PROMPT_VERSION,
    schema_version: str = SCHEMA_VERSION,
    model_id: str = MODEL_NAME,
) -> str:
    key_str = f"{chunk_content}---{supplied_context}---{prompt_version}---{schema_version}---{model_id}"
    return hashlib.sha256(key_str.encode("utf-8")).hexdigest()


class ExtractionStore:
    """Manages normalized persistence and retrieval for all extraction records and states."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        """Initializes tables and indices."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with get_db_connection(self.db_path) as conn:
            # 1. Documents Table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                regulator TEXT NOT NULL,
                regulator_authority TEXT,
                folder_id TEXT,
                category TEXT,
                doc_stem TEXT,
                document_title TEXT,
                act_number TEXT,
                enactment_date TEXT,
                total_chunks INTEGER,
                metadata_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            # 2. Manifest Chunks Table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS manifest_chunks (
                chunk_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                regulator TEXT NOT NULL,
                folder_id TEXT,
                category TEXT,
                chunk_index INTEGER,
                content_hash TEXT NOT NULL,
                char_count INTEGER,
                byte_count INTEGER,
                prev_chunk_id TEXT,
                next_chunk_id TEXT,
                section_locator TEXT,
                heading_hierarchy TEXT,
                metadata_json TEXT,
                FOREIGN KEY (document_id) REFERENCES documents (document_id)
            );
            """)

            # 3. Extraction Runs Table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS extraction_runs (
                run_id TEXT PRIMARY KEY,
                model_id TEXT NOT NULL,
                execution_route TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                prompt_hash TEXT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                status TEXT DEFAULT 'running',
                notes TEXT
            );
            """)

            # 4. Chunk Processing State Table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS chunk_processing_state (
                chunk_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                run_id TEXT,
                processing_status TEXT NOT NULL,
                review_status TEXT DEFAULT 'unreviewed',
                attempt_count INTEGER DEFAULT 0,
                last_error TEXT,
                cache_key TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chunk_id) REFERENCES manifest_chunks (chunk_id),
                FOREIGN KEY (document_id) REFERENCES documents (document_id)
            );
            """)

            # 5. Raw Extraction Outputs (Immutable storage)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_extractions (
                chunk_id TEXT PRIMARY KEY,
                cache_key TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chunk_id) REFERENCES manifest_chunks (chunk_id)
            );
            """)

            # 6. Normalized Entities Table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                entity_id TEXT PRIMARY KEY,
                chunk_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                local_entity_id TEXT NOT NULL,
                mention TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                description TEXT,
                identity_scope TEXT,
                evidence_json TEXT,
                requirement_details_json TEXT,
                review_status TEXT DEFAULT 'unreviewed',
                FOREIGN KEY (chunk_id) REFERENCES manifest_chunks (chunk_id),
                FOREIGN KEY (document_id) REFERENCES documents (document_id)
            );
            """)

            # 7. Normalized Relationships Table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS relationships (
                relationship_id TEXT PRIMARY KEY,
                chunk_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                local_relationship_id TEXT NOT NULL,
                source_entity_id TEXT NOT NULL,
                target_entity_id TEXT NOT NULL,
                relationship_type TEXT NOT NULL,
                description TEXT,
                conditions_json TEXT,
                exceptions_json TEXT,
                numeric_constraints_json TEXT,
                temporal_qualifiers_json TEXT,
                evidence_json TEXT,
                review_status TEXT DEFAULT 'unreviewed',
                FOREIGN KEY (chunk_id) REFERENCES manifest_chunks (chunk_id),
                FOREIGN KEY (document_id) REFERENCES documents (document_id)
            );
            """)

            # 8. Unresolved References Table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS unresolved_references (
                ref_id TEXT PRIMARY KEY,
                chunk_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                target_text TEXT NOT NULL,
                ref_type TEXT NOT NULL,
                context_needed TEXT,
                status TEXT DEFAULT 'pending',
                resolution_notes TEXT,
                FOREIGN KEY (chunk_id) REFERENCES manifest_chunks (chunk_id),
                FOREIGN KEY (document_id) REFERENCES documents (document_id)
            );
            """)

            # 9. Evidence Spans Table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS evidence_spans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chunk_id TEXT NOT NULL,
                exact_quote TEXT NOT NULL,
                char_start INTEGER,
                char_end INTEGER,
                page_number INTEGER,
                provision_locator TEXT,
                verified INTEGER DEFAULT 1,
                FOREIGN KEY (chunk_id) REFERENCES manifest_chunks (chunk_id)
            );
            """)

            # 10. Validation Failures Table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS validation_failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chunk_id TEXT NOT NULL,
                run_id TEXT,
                rule_name TEXT NOT NULL,
                error_details TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            # 11. Cross-Chunk Resolved Entities Table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS entity_resolutions (
                canonical_entity_id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                identity_scope TEXT,
                consolidated_description TEXT,
                mention_count INTEGER DEFAULT 1,
                chunk_ids_json TEXT,
                aliases_json TEXT,
                review_status TEXT DEFAULT 'unreviewed',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            # Indices for rapid querying
            conn.execute("CREATE INDEX IF NOT EXISTS idx_state_status ON chunk_processing_state (processing_status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_chunk ON entities (chunk_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON entities (entity_type);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_chunk ON relationships (chunk_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_type ON relationships (relationship_type);")
            conn.commit()

    def sync_manifest(self, manifest: Dict[str, Any]):
        """Populates or updates documents and manifest_chunks from the corpus manifest."""
        with get_db_connection(self.db_path) as conn:
            # Insert documents
            for doc_id, dmeta in manifest["documents"].items():
                conn.execute("""
                INSERT INTO documents (
                    document_id, regulator, regulator_authority, folder_id, category,
                    doc_stem, document_title, act_number, enactment_date, total_chunks, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    document_title=excluded.document_title,
                    total_chunks=excluded.total_chunks,
                    metadata_json=excluded.metadata_json;
                """, (
                    doc_id,
                    dmeta["regulator"],
                    dmeta.get("regulator_authority"),
                    dmeta["folder_id"],
                    dmeta["category"],
                    dmeta["doc_stem"],
                    dmeta.get("document_title"),
                    dmeta.get("act_number"),
                    dmeta.get("enactment_date"),
                    dmeta["total_chunks"],
                    json.dumps(dmeta),
                ))

            # Batch insert manifest chunks
            chunk_rows = []
            state_rows = []
            for chunk_id, cinfo in manifest["chunks"].items():
                headings_str = json.dumps(cinfo.get("heading_hierarchy", []))
                chunk_rows.append((
                    chunk_id,
                    cinfo["document_id"],
                    cinfo["file_path"],
                    cinfo["relative_path"],
                    cinfo["regulator"],
                    cinfo["folder_id"],
                    cinfo["category"],
                    cinfo["chunk_index"],
                    cinfo["content_hash"],
                    cinfo["char_count"],
                    cinfo["byte_count"],
                    cinfo["prev_chunk_id"],
                    cinfo["next_chunk_id"],
                    cinfo.get("section_locator"),
                    headings_str,
                    json.dumps(cinfo),
                ))
                initial_status = "empty" if cinfo["char_count"] == 0 else "pending"
                state_rows.append((chunk_id, cinfo["document_id"], initial_status))

            conn.executemany("""
            INSERT INTO manifest_chunks (
                chunk_id, document_id, file_path, relative_path, regulator,
                folder_id, category, chunk_index, content_hash, char_count,
                byte_count, prev_chunk_id, next_chunk_id, section_locator,
                heading_hierarchy, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chunk_id) DO UPDATE SET
                content_hash=excluded.content_hash,
                char_count=excluded.char_count,
                byte_count=excluded.byte_count;
            """, chunk_rows)

            conn.executemany("""
            INSERT INTO chunk_processing_state (
                chunk_id, document_id, processing_status, review_status, attempt_count
            ) VALUES (?, ?, ?, 'unreviewed', 0)
            ON CONFLICT(chunk_id) DO NOTHING;
            """, state_rows)

            conn.commit()

    def create_run(self, run_id: str, prompt_hash: str = "", notes: str = "") -> str:
        with get_db_connection(self.db_path) as conn:
            conn.execute("""
            INSERT INTO extraction_runs (
                run_id, model_id, execution_route, schema_version, prompt_version, prompt_hash, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET started_at=CURRENT_TIMESTAMP;
            """, (
                run_id,
                MODEL_NAME,
                EXECUTION_ROUTE,
                SCHEMA_VERSION,
                PROMPT_VERSION,
                prompt_hash,
                notes,
            ))
            conn.commit()
        return run_id

    def save_chunk_extraction(
        self,
        output: ChunkExtractionOutput,
        cache_key: str,
        run_id: str,
    ):
        """Persists validated extraction atomically, saving entities, relationships, references, and state."""
        with get_db_connection(self.db_path) as conn:
            chunk_id = output.chunk_id
            doc_id = output.document_id

            # 1. Save immutable raw output
            conn.execute("""
            INSERT INTO raw_extractions (chunk_id, cache_key, raw_json)
            VALUES (?, ?, ?)
            ON CONFLICT(chunk_id) DO UPDATE SET
                cache_key=excluded.cache_key,
                raw_json=excluded.raw_json,
                created_at=CURRENT_TIMESTAMP;
            """, (chunk_id, cache_key, output.model_dump_json()))

            # 2. Clean previous extractions for this chunk to maintain idempotency
            conn.execute("DELETE FROM entities WHERE chunk_id = ?", (chunk_id,))
            conn.execute("DELETE FROM relationships WHERE chunk_id = ?", (chunk_id,))
            conn.execute("DELETE FROM unresolved_references WHERE chunk_id = ?", (chunk_id,))
            conn.execute("DELETE FROM evidence_spans WHERE chunk_id = ?", (chunk_id,))

            # 3. Insert Entities
            for ent in output.entities:
                global_entity_id = f"{chunk_id}::{ent.local_entity_id}"
                req_json = ent.requirement_details.model_dump_json() if ent.requirement_details else None
                ev_json = json.dumps([ev.model_dump() for ev in ent.evidence_references])

                conn.execute("""
                INSERT INTO entities (
                    entity_id, chunk_id, document_id, local_entity_id, mention,
                    canonical_name, entity_type, description, identity_scope,
                    evidence_json, requirement_details_json, review_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unreviewed');
                """, (
                    global_entity_id,
                    chunk_id,
                    doc_id,
                    ent.local_entity_id,
                    ent.mention,
                    ent.canonical_name,
                    ent.entity_type,
                    ent.description,
                    ent.identity_scope,
                    ev_json,
                    req_json,
                ))

                # Insert evidence spans
                for ev in ent.evidence_references:
                    conn.execute("""
                    INSERT INTO evidence_spans (
                        chunk_id, exact_quote, char_start, char_end, page_number, provision_locator, verified
                    ) VALUES (?, ?, ?, ?, ?, ?, 1);
                    """, (
                        ev.source_chunk_id,
                        ev.exact_quote,
                        ev.char_start,
                        ev.char_end,
                        ev.page_number,
                        ev.provision_locator,
                    ))

            # 4. Insert Relationships
            for rel in output.relationships:
                global_rel_id = f"{chunk_id}::{rel.local_relationship_id}"
                src_global = f"{chunk_id}::{rel.source_entity_id}"
                tgt_global = f"{chunk_id}::{rel.target_entity_id}"
                cond_json = json.dumps([c.model_dump() for c in rel.conditions])
                exc_json = json.dumps(rel.exceptions)
                num_json = json.dumps([n.model_dump() for n in rel.numeric_constraints])
                temp_json = json.dumps([t.model_dump() for t in rel.temporal_qualifiers])
                ev_json = json.dumps([ev.model_dump() for ev in rel.evidence_references])

                conn.execute("""
                INSERT INTO relationships (
                    relationship_id, chunk_id, document_id, local_relationship_id,
                    source_entity_id, target_entity_id, relationship_type, description,
                    conditions_json, exceptions_json, numeric_constraints_json,
                    temporal_qualifiers_json, evidence_json, review_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unreviewed');
                """, (
                    global_rel_id,
                    chunk_id,
                    doc_id,
                    rel.local_relationship_id,
                    src_global,
                    tgt_global,
                    rel.relationship_type,
                    rel.description,
                    cond_json,
                    exc_json,
                    num_json,
                    temp_json,
                    ev_json,
                ))

            # 5. Insert Unresolved References
            for ref in output.unresolved_references:
                global_ref_id = f"{chunk_id}::{ref.ref_id}"
                conn.execute("""
                INSERT INTO unresolved_references (
                    ref_id, chunk_id, document_id, target_text, ref_type, context_needed, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending');
                """, (
                    global_ref_id,
                    chunk_id,
                    doc_id,
                    ref.target_text,
                    ref.ref_type,
                    ref.context_needed,
                ))

            # 6. Update Chunk State
            conn.execute("""
            UPDATE chunk_processing_state SET
                processing_status = ?,
                run_id = ?,
                cache_key = ?,
                attempt_count = attempt_count + 1,
                last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE chunk_id = ?;
            """, (output.extraction_status, run_id, cache_key, chunk_id))

    def save_chunk_extractions_batch(
        self,
        outputs_and_meta: List[Tuple[ChunkExtractionOutput, str]],
        run_id: str,
    ):
        """Persists a batch of validated extractions in a single atomic SQLite transaction."""
        if not outputs_and_meta:
            return

        raw_rows = []
        entity_rows = []
        evidence_rows = []
        rel_rows = []
        unres_rows = []
        state_rows = []
        chunk_ids = []

        for output, cache_key in outputs_and_meta:
            chunk_id = output.chunk_id
            doc_id = output.document_id
            chunk_ids.append(chunk_id)

            raw_rows.append((chunk_id, cache_key, output.model_dump_json()))
            state_rows.append((output.extraction_status, run_id, cache_key, chunk_id))

            for ent in output.entities:
                global_entity_id = f"{chunk_id}::{ent.local_entity_id}"
                req_json = ent.requirement_details.model_dump_json() if ent.requirement_details else None
                ev_json = json.dumps([ev.model_dump() for ev in ent.evidence_references])

                entity_rows.append((
                    global_entity_id,
                    chunk_id,
                    doc_id,
                    ent.local_entity_id,
                    ent.mention,
                    ent.canonical_name,
                    ent.entity_type,
                    ent.description,
                    ent.identity_scope,
                    ev_json,
                    req_json,
                ))

                for ev in ent.evidence_references:
                    evidence_rows.append((
                        ev.source_chunk_id,
                        ev.exact_quote,
                        ev.char_start,
                        ev.char_end,
                        ev.page_number,
                        ev.provision_locator,
                    ))

            for rel in output.relationships:
                global_rel_id = f"{chunk_id}::{rel.local_relationship_id}"
                src_global = f"{chunk_id}::{rel.source_entity_id}"
                tgt_global = f"{chunk_id}::{rel.target_entity_id}"
                cond_json = json.dumps([c.model_dump() for c in rel.conditions])
                exc_json = json.dumps(rel.exceptions)
                num_json = json.dumps([n.model_dump() for n in rel.numeric_constraints])
                temp_json = json.dumps([t.model_dump() for t in rel.temporal_qualifiers])
                ev_json = json.dumps([ev.model_dump() for ev in rel.evidence_references])

                rel_rows.append((
                    global_rel_id,
                    chunk_id,
                    doc_id,
                    rel.local_relationship_id,
                    src_global,
                    tgt_global,
                    rel.relationship_type,
                    rel.description,
                    cond_json,
                    exc_json,
                    num_json,
                    temp_json,
                    ev_json,
                ))

            for ref in output.unresolved_references:
                global_ref_id = f"{chunk_id}::{ref.ref_id}"
                unres_rows.append((
                    global_ref_id,
                    chunk_id,
                    doc_id,
                    ref.target_text,
                    ref.ref_type,
                    ref.context_needed,
                ))

        with get_db_connection(self.db_path) as conn:
            # 1. Raw extractions
            conn.executemany("""
            INSERT INTO raw_extractions (chunk_id, cache_key, raw_json)
            VALUES (?, ?, ?)
            ON CONFLICT(chunk_id) DO UPDATE SET
                cache_key=excluded.cache_key,
                raw_json=excluded.raw_json,
                created_at=CURRENT_TIMESTAMP;
            """, raw_rows)

            # 2. Delete existing entities/relationships for these chunks
            # Chunk ids deletion in batches
            for i in range(0, len(chunk_ids), 500):
                sub_ids = chunk_ids[i:i + 500]
                placeholders = ",".join("?" for _ in sub_ids)
                conn.execute(f"DELETE FROM entities WHERE chunk_id IN ({placeholders});", sub_ids)
                conn.execute(f"DELETE FROM relationships WHERE chunk_id IN ({placeholders});", sub_ids)
                conn.execute(f"DELETE FROM unresolved_references WHERE chunk_id IN ({placeholders});", sub_ids)
                conn.execute(f"DELETE FROM evidence_spans WHERE chunk_id IN ({placeholders});", sub_ids)

            # 3. Insert Entities
            if entity_rows:
                conn.executemany("""
                INSERT INTO entities (
                    entity_id, chunk_id, document_id, local_entity_id, mention,
                    canonical_name, entity_type, description, identity_scope,
                    evidence_json, requirement_details_json, review_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unreviewed');
                """, entity_rows)

            # 4. Insert Relationships
            if rel_rows:
                conn.executemany("""
                INSERT INTO relationships (
                    relationship_id, chunk_id, document_id, local_relationship_id,
                    source_entity_id, target_entity_id, relationship_type, description,
                    conditions_json, exceptions_json, numeric_constraints_json,
                    temporal_qualifiers_json, evidence_json, review_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unreviewed');
                """, rel_rows)

            # 5. Insert Evidence Spans
            if evidence_rows:
                conn.executemany("""
                INSERT INTO evidence_spans (
                    chunk_id, exact_quote, char_start, char_end, page_number, provision_locator, verified
                ) VALUES (?, ?, ?, ?, ?, ?, 1);
                """, evidence_rows)

            # 6. Insert Unresolved References
            if unres_rows:
                conn.executemany("""
                INSERT INTO unresolved_references (
                    ref_id, chunk_id, document_id, target_text, ref_type, context_needed, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending');
                """, unres_rows)

            # 7. Update Chunk State
            conn.executemany("""
            UPDATE chunk_processing_state SET
                processing_status = ?,
                run_id = ?,
                cache_key = ?,
                attempt_count = attempt_count + 1,
                last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE chunk_id = ?;
            """, state_rows)

            conn.commit()

    def record_failure(self, chunk_id: str, run_id: str, rule_name: str, error_details: str):
        with get_db_connection(self.db_path) as conn:
            conn.execute("""
            INSERT INTO validation_failures (chunk_id, run_id, rule_name, error_details)
            VALUES (?, ?, ?, ?);
            """, (chunk_id, run_id, rule_name, error_details))
            conn.execute("""
            UPDATE chunk_processing_state SET
                processing_status = 'validation_failed',
                run_id = ?,
                attempt_count = attempt_count + 1,
                last_error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE chunk_id = ?;
            """, (run_id, f"[{rule_name}] {error_details}", chunk_id))
            conn.commit()

    def get_manifest_counts(self) -> Dict[str, int]:
        with get_db_connection(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM manifest_chunks;").fetchone()[0]
            cursor = conn.execute("""
                SELECT processing_status, COUNT(*) as cnt
                FROM chunk_processing_state
                GROUP BY processing_status;
            """)
            counts = {row["processing_status"]: row["cnt"] for row in cursor.fetchall()}
            counts["total"] = total
            return counts

    def get_pending_chunks(self, limit: int = 50, regulator: Optional[str] = None) -> List[sqlite3.Row]:
        with get_db_connection(self.db_path) as conn:
            query = """
                SELECT c.*, s.processing_status, s.attempt_count
                FROM manifest_chunks c
                JOIN chunk_processing_state s ON c.chunk_id = s.chunk_id
                WHERE s.processing_status = 'pending'
            """
            params = []
            if regulator:
                query += " AND c.regulator = ?"
                params.append(regulator)
            query += " ORDER BY c.regulator, c.folder_id, c.chunk_index ASC LIMIT ?"
            params.append(limit)
            return conn.execute(query, params).fetchall()

    def get_all_entities(self) -> List[sqlite3.Row]:
        with get_db_connection(self.db_path) as conn:
            return conn.execute("SELECT * FROM entities;").fetchall()

    def get_all_relationships(self) -> List[sqlite3.Row]:
        with get_db_connection(self.db_path) as conn:
            return conn.execute("SELECT * FROM relationships;").fetchall()

    def get_all_evidence(self) -> List[sqlite3.Row]:
        with get_db_connection(self.db_path) as conn:
            return conn.execute("SELECT * FROM evidence_spans;").fetchall()

    def get_all_unresolved(self) -> List[sqlite3.Row]:
        with get_db_connection(self.db_path) as conn:
            return conn.execute("SELECT * FROM unresolved_references;").fetchall()
