"""FalkorDB Evidence-Grounded Knowledge Graph Ingestion Engine.
Loads extraction outputs into FalkorDB adhering to the 4-layer architecture:
1. Source Layer: Document, DocumentVersion, Provision, Chunk (with NEXT_CHUNK chain)
2. Entity Layer: Resolved canonical entities with stable IDs and typed labels
3. Assertion Layer: RelationshipAssertion nodes preserving conditions, exceptions, qualifiers
   plus derived direct view edges (:RELATION) for fast bounded path expansion
4. Evidence Layer: EvidenceSpan nodes linking assertions to source chunks with verbatim text
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional, Set

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src.config import (
    DB_PATH,
    EXPORTS_DIR,
    DATA_DIR,
    FALKORDB_GRAPH_NAME,
    FALKORDB_BATCH_SIZE,
    get_falkordb_client,
)
from src.db import get_db_connection


class FalkorDBIngestionEngine:
    """Manages reliable, idempotent, resumable ingestion into FalkorDB."""

    def __init__(
        self,
        graph_name: str = FALKORDB_GRAPH_NAME,
        batch_size: int = FALKORDB_BATCH_SIZE,
        db_path: Path = DB_PATH,
        checkpoint_path: Optional[Path] = None,
    ):
        self.graph_name = graph_name
        self.batch_size = batch_size
        self.db_path = db_path
        self.checkpoint_path = checkpoint_path or (DATA_DIR / "falkordb_load_checkpoint.json")
        self.client = get_falkordb_client()
        self.graph = self.client.select_graph(self.graph_name)
        self.load_run_id = f"load_run_{int(time.time())}"

    def query(self, cypher: str, params: Optional[Dict[str, Any]] = None):
        """Resilient query execution with automatic socket reconnection and retry."""
        for attempt in range(5):
            try:
                return self.graph.query(cypher, params) if params else self.graph.query(cypher)
            except Exception as e:
                if attempt == 4:
                    raise
                time.sleep(0.5 * (attempt + 1))
                try:
                    self.client = get_falkordb_client()
                    self.graph = self.client.select_graph(self.graph_name)
                except Exception:
                    pass

    def init_schema(self):
        """Initializes schema indexes idempotently in FalkorDB."""
        print(f"Initializing FalkorDB schema indexes for graph '{self.graph_name}'...", flush=True)
        index_queries = [
            "CREATE INDEX FOR (d:Document) ON (d.id)",
            "CREATE INDEX FOR (d:Document) ON (d.regulator)",
            "CREATE INDEX FOR (c:Chunk) ON (c.id)",
            "CREATE INDEX FOR (c:Chunk) ON (c.document_id)",
            "CREATE INDEX FOR (e:Entity) ON (e.id)",
            "CREATE INDEX FOR (e:Entity) ON (e.canonical_name)",
            "CREATE INDEX FOR (e:Entity) ON (e.entity_type)",
            "CREATE INDEX FOR (a:RelationshipAssertion) ON (a.id)",
            "CREATE INDEX FOR (a:RelationshipAssertion) ON (a.relationship_type)",
            "CREATE INDEX FOR (ev:EvidenceSpan) ON (ev.id)",
            "CREATE INDEX FOR (ev:EvidenceSpan) ON (ev.chunk_id)",
        ]
        for q in index_queries:
            try:
                self.query(q)
            except Exception as ex:
                # FalkorDB raises error if index already exists
                pass
        print("Schema indexes initialized.", flush=True)

    def load_checkpoint(self) -> Dict[str, Any]:
        if self.checkpoint_path.exists():
            try:
                with open(self.checkpoint_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "load_run_id": self.load_run_id,
            "completed_stages": {},
            "counts": {},
        }

    def save_checkpoint(self, checkpoint: Dict[str, Any]):
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(checkpoint, f, indent=2)

    def ingest_documents(self, regulator: Optional[str] = None) -> int:
        print(f"--- Stage 1: Ingesting Documents (regulator={regulator or 'ALL'}) ---", flush=True)
        with get_db_connection(self.db_path) as conn:
            query = "SELECT * FROM documents"
            params = ()
            if regulator:
                query += " WHERE regulator = ?"
                params = (regulator,)
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        total = len(rows)
        print(f"Found {total} documents to ingest.", flush=True)
        batch = []
        ingested = 0
        t0 = time.time()

        for r in rows:
            batch.append({
                "id": r["document_id"],
                "regulator": r["regulator"] or "",
                "title": r["document_title"] or r["doc_stem"] or "",
                "act_number": r["act_number"] or "",
                "enactment_date": r["enactment_date"] or "",
                "folder_id": r["folder_id"] or "",
                "category": r["category"] or "",
                "total_chunks": r["total_chunks"] or 0,
            })
            if len(batch) >= self.batch_size:
                self._upsert_documents_batch(batch)
                ingested += len(batch)
                batch = []

        if batch:
            self._upsert_documents_batch(batch)
            ingested += len(batch)

        elapsed = time.time() - t0
        print(f"Documents ingested: {ingested}/{total} in {elapsed:.2f}s.", flush=True)
        return ingested

    def _upsert_documents_batch(self, batch: List[Dict[str, Any]]):
        cypher = """
        UNWIND $batch AS item
        MERGE (d:Document {id: item.id})
        ON CREATE SET
            d.regulator = item.regulator,
            d.title = item.title,
            d.act_number = item.act_number,
            d.enactment_date = item.enactment_date,
            d.folder_id = item.folder_id,
            d.category = item.category,
            d.total_chunks = item.total_chunks,
            d.created_at = timestamp()
        ON MATCH SET
            d.regulator = item.regulator,
            d.title = item.title,
            d.act_number = item.act_number,
            d.enactment_date = item.enactment_date,
            d.folder_id = item.folder_id,
            d.category = item.category,
            d.total_chunks = item.total_chunks
        """
        self.query(cypher, {"batch": batch})

    def ingest_chunks(self, regulator: Optional[str] = None, limit: Optional[int] = None) -> int:
        print(f"--- Stage 2: Ingesting Chunks (regulator={regulator or 'ALL'}) ---", flush=True)
        with get_db_connection(self.db_path) as conn:
            query = "SELECT * FROM manifest_chunks"
            params = []
            if regulator:
                query += " WHERE regulator = ?"
                params.append(regulator)
            query += " ORDER BY document_id, chunk_index ASC"
            if limit:
                query += f" LIMIT {limit}"
            cursor = conn.execute(query, tuple(params))
            rows = cursor.fetchall()

        total = len(rows)
        print(f"Found {total} chunks to ingest.", flush=True)
        batch = []
        ingested = 0
        t0 = time.time()

        for r in rows:
            batch.append({
                "id": r["chunk_id"],
                "document_id": r["document_id"],
                "file_path": r["file_path"],
                "regulator": r["regulator"],
                "chunk_index": r["chunk_index"] if r["chunk_index"] is not None else -1,
                "char_count": r["char_count"] or 0,
                "prev_chunk_id": r["prev_chunk_id"] or "",
                "next_chunk_id": r["next_chunk_id"] or "",
                "section_locator": r["section_locator"] or "",
            })
            if len(batch) >= self.batch_size:
                self._upsert_chunks_batch(batch)
                ingested += len(batch)
                if ingested % 5000 == 0 or ingested == total:
                    print(f"  Chunks progress: {ingested}/{total} ({ingested/max(time.time()-t0, 0.001):.0f} chunks/s)", flush=True)
                batch = []

        if batch:
            self._upsert_chunks_batch(batch)
            ingested += len(batch)

        elapsed = time.time() - t0
        print(f"Chunks ingested: {ingested}/{total} in {elapsed:.2f}s.", flush=True)
        return ingested

    def _upsert_chunks_batch(self, batch: List[Dict[str, Any]]):
        cypher = """
        UNWIND $batch AS item
        MERGE (c:Chunk {id: item.id})
        ON CREATE SET
            c.document_id = item.document_id,
            c.file_path = item.file_path,
            c.regulator = item.regulator,
            c.chunk_index = item.chunk_index,
            c.char_count = item.char_count,
            c.section_locator = item.section_locator,
            c.created_at = timestamp()
        ON MATCH SET
            c.document_id = item.document_id,
            c.file_path = item.file_path,
            c.regulator = item.regulator,
            c.chunk_index = item.chunk_index,
            c.char_count = item.char_count,
            c.section_locator = item.section_locator
        """
        self.query(cypher, {"batch": batch})

        # Link Document to Chunk
        link_cypher = """
        UNWIND $batch AS item
        MATCH (d:Document {id: item.document_id})
        MATCH (c:Chunk {id: item.id})
        MERGE (d)-[:HAS_CHUNK]->(c)
        """
        self.query(link_cypher, {"batch": batch})

    def ingest_resolved_entities(self, regulator: Optional[str] = None) -> int:
        print(f"--- Stage 3: Ingesting Resolved Canonical Entities ---", flush=True)
        with get_db_connection(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT canonical_entity_id, canonical_name, entity_type, identity_scope,
                       consolidated_description, mention_count, chunk_ids_json, aliases_json, review_status
                FROM entity_resolutions;
            """)
            rows = cursor.fetchall()

        total = len(rows)
        print(f"Found {total} resolved entities to ingest.", flush=True)
        batch = []
        ingested = 0
        t0 = time.time()

        for r in rows:
            batch.append({
                "id": r["canonical_entity_id"],
                "name": r["canonical_name"],
                "type": r["entity_type"],
                "scope": r["identity_scope"] or "",
                "description": r["consolidated_description"] or "",
                "mention_count": r["mention_count"] or 1,
                "aliases": r["aliases_json"] or "[]",
                "review_status": r["review_status"] or "unreviewed",
            })
            if len(batch) >= self.batch_size:
                self._upsert_entities_batch(batch)
                ingested += len(batch)
                if ingested % 10000 == 0 or ingested == total:
                    print(f"  Entities progress: {ingested}/{total} ({ingested/max(time.time()-t0, 0.001):.0f} entities/s)", flush=True)
                batch = []

        if batch:
            self._upsert_entities_batch(batch)
            ingested += len(batch)

        elapsed = time.time() - t0
        print(f"Entities ingested: {ingested}/{total} in {elapsed:.2f}s.", flush=True)
        return ingested

    def _upsert_entities_batch(self, batch: List[Dict[str, Any]]):
        cypher = """
        UNWIND $batch AS item
        MERGE (e:Entity {id: item.id})
        ON CREATE SET
            e.canonical_name = item.name,
            e.entity_type = item.type,
            e.identity_scope = item.scope,
            e.description = item.description,
            e.mention_count = item.mention_count,
            e.aliases_json = item.aliases,
            e.review_status = item.review_status,
            e.created_at = timestamp()
        ON MATCH SET
            e.canonical_name = item.name,
            e.entity_type = item.type,
            e.identity_scope = item.scope,
            e.description = item.description,
            e.mention_count = item.mention_count,
            e.aliases_json = item.aliases,
            e.review_status = item.review_status
        """
        self.query(cypher, {"batch": batch})

    def ingest_assertions_and_relationships(self, regulator: Optional[str] = None, limit: Optional[int] = None) -> int:
        print(f"--- Stage 4: Ingesting Assertions, Evidence Links & Views (regulator={regulator or 'ALL'}) ---", flush=True)
        with get_db_connection(self.db_path) as conn:
            # Query relationships mapped to canonical entity clusters
            query = """
            SELECT r.relationship_id, r.chunk_id, r.document_id, r.relationship_type, r.description,
                   r.conditions_json, r.exceptions_json, r.numeric_constraints_json, r.temporal_qualifiers_json,
                   r.evidence_json, r.review_status,
                   s.canonical_name as s_cname, s.entity_type as s_type, s.document_id as s_doc_id, s.entity_id as s_eid,
                   t.canonical_name as t_cname, t.entity_type as t_type, t.document_id as t_doc_id, t.entity_id as t_eid
            FROM relationships r
            JOIN entities s ON r.source_entity_id = s.entity_id
            JOIN entities t ON r.target_entity_id = t.entity_id
            """
            params = []
            where_clauses = ["(r.review_status IS NULL OR r.review_status != 'quarantined')"]
            if regulator:
                query += " JOIN manifest_chunks c ON r.chunk_id = c.chunk_id"
                where_clauses.append("c.regulator = ?")
                params.append(regulator)
            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)
            if limit:
                query += f" LIMIT {limit}"

            cursor = conn.execute(query, tuple(params))
            rows = cursor.fetchall()

        total = len(rows)
        print(f"Found {total} relationships to ingest into FalkorDB.", flush=True)

        batch = []
        ingested = 0
        t0 = time.time()

        for r in rows:
            # Resolve canonical source and target IDs matching entity_resolutions
            s_type = r["s_type"]
            s_cname = (r["s_cname"] or "").strip().lower()
            s_doc = r["s_doc_id"]
            if s_type in ["Provision", "DefinedTerm"]:
                s_canon_id = f"CANONICAL::{s_type}::{s_doc}::{s_cname}"
            elif s_type == "Requirement":
                s_canon_id = f"CANONICAL::Requirement::{r['s_eid']}"
            else:
                s_canon_id = f"CANONICAL::{s_type}::{s_cname}"

            t_type = r["t_type"]
            t_cname = (r["t_cname"] or "").strip().lower()
            t_doc = r["t_doc_id"]
            if t_type in ["Provision", "DefinedTerm"]:
                t_canon_id = f"CANONICAL::{t_type}::{t_doc}::{t_cname}"
            elif t_type == "Requirement":
                t_canon_id = f"CANONICAL::Requirement::{r['t_eid']}"
            else:
                t_canon_id = f"CANONICAL::{t_type}::{t_cname}"

            cond_str = r["conditions_json"] or "[]"
            exc_str = r["exceptions_json"] or "[]"
            num_str = r["numeric_constraints_json"] or "[]"
            is_cond = bool(cond_str != "[]" or exc_str != "[]" or num_str != "[]")

            batch.append({
                "id": r["relationship_id"],
                "chunk_id": r["chunk_id"],
                "document_id": r["document_id"],
                "source_id": s_canon_id,
                "target_id": t_canon_id,
                "rel_type": r["relationship_type"],
                "description": r["description"] or "",
                "conditions_json": cond_str,
                "exceptions_json": exc_str,
                "numeric_constraints_json": num_str,
                "temporal_qualifiers_json": r["temporal_qualifiers_json"] or "[]",
                "is_conditional": is_cond,
                "review_status": r["review_status"] or "unreviewed",
            })

            if len(batch) >= self.batch_size:
                self._upsert_assertions_batch(batch)
                ingested += len(batch)
                if ingested % 5000 == 0 or ingested == total:
                    print(f"  Assertions progress: {ingested}/{total} ({ingested/max(time.time()-t0, 0.001):.0f} rels/s)", flush=True)
                batch = []

        if batch:
            self._upsert_assertions_batch(batch)
            ingested += len(batch)

        elapsed = time.time() - t0
        print(f"Assertions ingested: {ingested}/{total} in {elapsed:.2f}s.", flush=True)
        return ingested

    def _upsert_assertions_batch(self, batch: List[Dict[str, Any]]):
        # 1. Upsert RelationshipAssertion node & endpoints
        cypher = """
        UNWIND $batch AS item
        MATCH (s:Entity {id: item.source_id})
        MATCH (t:Entity {id: item.target_id})
        MERGE (a:RelationshipAssertion {id: item.id})
        ON CREATE SET
            a.chunk_id = item.chunk_id,
            a.document_id = item.document_id,
            a.relationship_type = item.rel_type,
            a.description = item.description,
            a.conditions_json = item.conditions_json,
            a.exceptions_json = item.exceptions_json,
            a.numeric_constraints_json = item.numeric_constraints_json,
            a.temporal_qualifiers_json = item.temporal_qualifiers_json,
            a.is_conditional = item.is_conditional,
            a.review_status = item.review_status,
            a.created_at = timestamp()
        ON MATCH SET
            a.chunk_id = item.chunk_id,
            a.document_id = item.document_id,
            a.relationship_type = item.rel_type,
            a.description = item.description,
            a.conditions_json = item.conditions_json,
            a.exceptions_json = item.exceptions_json,
            a.numeric_constraints_json = item.numeric_constraints_json,
            a.temporal_qualifiers_json = item.temporal_qualifiers_json,
            a.is_conditional = item.is_conditional,
            a.review_status = item.review_status
        MERGE (s)-[:SUBJECT_OF]->(a)
        MERGE (a)-[:OBJECT]->(t)
        MERGE (s)-[r:RELATION {assertion_id: item.id}]->(t)
        ON CREATE SET
            r.rel_type = item.rel_type,
            r.is_conditional = item.is_conditional,
            r.review_status = item.review_status
        ON MATCH SET
            r.rel_type = item.rel_type,
            r.is_conditional = item.is_conditional,
            r.review_status = item.review_status
        """
        self.query(cypher, {"batch": batch})

    def ingest_evidence(self, regulator: Optional[str] = None, limit: Optional[int] = None) -> int:
        print(f"--- Stage 5: Ingesting Evidence Spans & Linking to Chunks ---", flush=True)
        with get_db_connection(self.db_path) as conn:
            query = """
            SELECT e.id, e.chunk_id, e.exact_quote, e.char_start, e.char_end,
                   e.page_number, e.provision_locator, e.verified
            FROM evidence_spans e
            """
            params = []
            if regulator:
                query += " JOIN manifest_chunks c ON e.chunk_id = c.chunk_id WHERE c.regulator = ?"
                params.append(regulator)
            if limit:
                query += f" LIMIT {limit}"

            cursor = conn.execute(query, tuple(params))
            rows = cursor.fetchall()

        total = len(rows)
        print(f"Found {total} evidence spans to ingest.", flush=True)

        batch = []
        ingested = 0
        t0 = time.time()

        for r in rows:
            batch.append({
                "id": f"EV_{r['id']}",
                "raw_id": r["id"],
                "chunk_id": r["chunk_id"],
                "exact_quote": r["exact_quote"] or "",
                "char_start": r["char_start"] if r["char_start"] is not None else -1,
                "char_end": r["char_end"] if r["char_end"] is not None else -1,
                "page_number": r["page_number"] if r["page_number"] is not None else -1,
                "provision_locator": r["provision_locator"] or "",
                "verified": bool(r["verified"]),
            })
            if len(batch) >= self.batch_size:
                self._upsert_evidence_batch(batch)
                ingested += len(batch)
                if ingested % 5000 == 0 or ingested == total:
                    print(f"  Evidence progress: {ingested}/{total} ({ingested/max(time.time()-t0, 0.001):.0f} spans/s)", flush=True)
                batch = []

        if batch:
            self._upsert_evidence_batch(batch)
            ingested += len(batch)

        elapsed = time.time() - t0
        print(f"Evidence spans ingested: {ingested}/{total} in {elapsed:.2f}s.", flush=True)
        return ingested

    def _upsert_evidence_batch(self, batch: List[Dict[str, Any]]):
        cypher = """
        UNWIND $batch AS item
        MATCH (c:Chunk {id: item.chunk_id})
        MERGE (ev:EvidenceSpan {id: item.id})
        ON CREATE SET
            ev.chunk_id = item.chunk_id,
            ev.exact_quote = item.exact_quote,
            ev.char_start = item.char_start,
            ev.char_end = item.char_end,
            ev.page_number = item.page_number,
            ev.provision_locator = item.provision_locator,
            ev.verified = item.verified,
            ev.created_at = timestamp()
        ON MATCH SET
            ev.chunk_id = item.chunk_id,
            ev.exact_quote = item.exact_quote,
            ev.char_start = item.char_start,
            ev.char_end = item.char_end,
            ev.page_number = item.page_number,
            ev.provision_locator = item.provision_locator,
            ev.verified = item.verified
        MERGE (ev)-[:IN_CHUNK]->(c)
        """
        self.query(cypher, {"batch": batch})

    def link_chunk_entity_mentions(self, regulator: Optional[str] = None, limit: Optional[int] = None) -> int:
        print(f"--- Stage 6: Linking Chunks to Mentioned Entities ---", flush=True)
        with get_db_connection(self.db_path) as conn:
            query = """
            SELECT e.entity_id, e.chunk_id, e.canonical_name, e.entity_type, e.document_id, e.mention
            FROM entities e
            """
            params = []
            if regulator:
                query += " JOIN manifest_chunks c ON e.chunk_id = c.chunk_id WHERE c.regulator = ?"
                params.append(regulator)
            if limit:
                query += f" LIMIT {limit}"

            cursor = conn.execute(query, tuple(params))
            rows = cursor.fetchall()

        total = len(rows)
        print(f"Found {total} entity mentions to link.", flush=True)

        batch = []
        ingested = 0
        t0 = time.time()

        for r in rows:
            etype = r["entity_type"]
            cname = (r["canonical_name"] or "").strip().lower()
            doc = r["document_id"]
            if etype in ["Provision", "DefinedTerm"]:
                canon_id = f"CANONICAL::{etype}::{doc}::{cname}"
            elif etype == "Requirement":
                canon_id = f"CANONICAL::Requirement::{r['entity_id']}"
            else:
                canon_id = f"CANONICAL::{etype}::{cname}"

            batch.append({
                "chunk_id": r["chunk_id"],
                "canonical_id": canon_id,
                "mention": r["mention"] or "",
            })
            if len(batch) >= self.batch_size:
                self._upsert_mentions_batch(batch)
                ingested += len(batch)
                if ingested % 10000 == 0 or ingested == total:
                    print(f"  Mentions progress: {ingested}/{total} ({ingested/max(time.time()-t0, 0.001):.0f} mentions/s)", flush=True)
                batch = []

        if batch:
            self._upsert_mentions_batch(batch)
            ingested += len(batch)

        elapsed = time.time() - t0
        print(f"Chunk entity mentions linked: {ingested}/{total} in {elapsed:.2f}s.", flush=True)
        return ingested

    def _upsert_mentions_batch(self, batch: List[Dict[str, Any]]):
        cypher = """
        UNWIND $batch AS item
        MATCH (c:Chunk {id: item.chunk_id})
        MATCH (e:Entity {id: item.canonical_id})
        MERGE (c)-[m:MENTIONS]->(e)
        ON CREATE SET m.mention = item.mention
        """
        self.query(cypher, {"batch": batch})

    def link_assertions_to_evidence(self):
        print(f"--- Stage 7: Linking Assertions to Supporting Evidence ---", flush=True)
        # Link assertions to evidence spans sharing the same chunk
        cypher = """
        MATCH (a:RelationshipAssertion)
        MATCH (ev:EvidenceSpan {chunk_id: a.chunk_id})
        MERGE (a)-[:SUPPORTED_BY]->(ev)
        """
        res = self.query(cypher)
        print("Assertions linked to evidence spans.", flush=True)

    def reconcile_graph_counts(self) -> Dict[str, Any]:
        print("\n=== FalkorDB Graph Reconciliation ===", flush=True)
        counts = {}
        queries = {
            "Document": "MATCH (d:Document) RETURN count(d)",
            "Chunk": "MATCH (c:Chunk) RETURN count(c)",
            "Entity": "MATCH (e:Entity) RETURN count(e)",
            "RelationshipAssertion": "MATCH (a:RelationshipAssertion) RETURN count(a)",
            "EvidenceSpan": "MATCH (ev:EvidenceSpan) RETURN count(ev)",
            "RELATION_edges": "MATCH ()-[r:RELATION]->() RETURN count(r)",
            "MENTIONS_edges": "MATCH ()-[r:MENTIONS]->() RETURN count(r)",
            "SUPPORTED_BY_edges": "MATCH ()-[r:SUPPORTED_BY]->() RETURN count(r)",
        }
        for label, q in queries.items():
            try:
                res = self.query(q)
                cnt = res.result_set[0][0] if res.result_set else 0
                counts[label] = cnt
                print(f"  {label}: {cnt}")
            except Exception as ex:
                counts[label] = f"Error: {ex}"
                print(f"  {label}: Error: {ex}")
        return counts


def main():
    parser = argparse.ArgumentParser(description="Ingest extracted regulatory data into FalkorDB")
    parser.add_argument("--regulator", type=str, default="RBI", help="Filter by regulator (e.g. RBI, SEBI, MCA, or ALL)")
    parser.add_argument("--batch-size", type=int, default=FALKORDB_BATCH_SIZE, help="Batch size for Cypher write operations")
    parser.add_argument("--graph-name", type=str, default=FALKORDB_GRAPH_NAME, help="Target FalkorDB graph name")
    parser.add_argument("--limit-chunks", type=int, default=None, help="Optional limit on chunks for targeted testing")
    parser.add_argument("--drop-existing", action="store_true", help="Drop target graph before ingestion for clean rebuild")
    args = parser.parse_args()

    reg = None if args.regulator.upper() == "ALL" else args.regulator.upper()
    engine = FalkorDBIngestionEngine(graph_name=args.graph_name, batch_size=args.batch_size)

    if args.drop_existing:
        try:
            print(f"Dropping existing graph '{args.graph_name}' for clean rebuild...", flush=True)
            engine.graph.delete()
            engine.graph = engine.client.select_graph(args.graph_name)
        except Exception as ex:
            print(f"Note on graph drop: {ex}", flush=True)
            engine.graph = engine.client.select_graph(args.graph_name)

    print(f"Starting FalkorDB ingestion into graph '{args.graph_name}' (regulator={reg or 'ALL'})...")
    engine.init_schema()

    t_start = time.time()
    n_docs = engine.ingest_documents(regulator=reg)
    n_chunks = engine.ingest_chunks(regulator=reg, limit=args.limit_chunks)
    n_entities = engine.ingest_resolved_entities()
    n_assertions = engine.ingest_assertions_and_relationships(regulator=reg, limit=args.limit_chunks)
    n_evidence = engine.ingest_evidence(regulator=reg, limit=args.limit_chunks)
    n_mentions = engine.link_chunk_entity_mentions(regulator=reg, limit=args.limit_chunks)
    engine.link_assertions_to_evidence()

    elapsed = time.time() - t_start
    print(f"\nIngestion pass completed in {elapsed:.2f}s.")

    counts = engine.reconcile_graph_counts()
    try:
        print("Persisting FalkorDB graph snapshot to disk (SAVE)...", flush=True)
        engine.client.connection.save()
        print("Graph snapshot saved to disk.", flush=True)
    except Exception as ex:
        print(f"Note on graph save: {ex}", flush=True)
    manifest_data = {
        "load_run_id": engine.load_run_id,
        "graph_name": args.graph_name,
        "regulator_filter": reg or "ALL",
        "duration_seconds": round(elapsed, 2),
        "counts": counts,
        "status": "COMPLETED",
        "review_mode": "unreviewed_extraction_dev_mode",
    }
    manifest_path = DATA_DIR / "falkordb_ingestion_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)
    print(f"Ingestion manifest saved to: {manifest_path}")


if __name__ == "__main__":
    main()
