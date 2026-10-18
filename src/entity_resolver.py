"""Conservative cross-chunk entity resolution engine.
Prevents erroneous conflation of provisions, defined terms, and requirements across instruments,
while indexing aliases and linking shared authorities and entity categories.
Memory-efficient batch streaming.
"""

import json
from collections import defaultdict
from typing import Dict, List, Any
from src.db import ExtractionStore, get_db_connection


class ConservativeEntityResolver:
    """Performs conservative entity resolution maintaining scope integrity."""

    def __init__(self, store: ExtractionStore):
        self.store = store

    def resolve_entities(self) -> Dict[str, Any]:
        """Resolves extracted entity mentions conservatively across chunks and records resolutions."""
        print("Starting conservative cross-chunk entity resolution...", flush=True)

        # Pass 1: Stream and aggregate into cluster metadata (lightweight representation)
        # cluster_key -> { 'canonical_name': str, 'entity_type': str, 'scope': str, 'desc': str, 'mentions': set, 'chunks': set, 'count': int }
        clusters: Dict[str, Dict[str, Any]] = {}
        total_mentions = 0

        with get_db_connection(self.store.db_path) as conn:
            cursor = conn.execute("""
                SELECT entity_id, chunk_id, document_id, local_entity_id, mention,
                       canonical_name, entity_type, description, identity_scope
                FROM entities;
            """)
            for row in cursor:
                total_mentions += 1
                etype = row["entity_type"]
                cname = row["canonical_name"].strip()
                doc_id = row["document_id"]
                cid = row["chunk_id"]
                mention = row["mention"]
                desc = row["description"]
                scope = row["identity_scope"] or ""

                if etype in ["Provision", "DefinedTerm"]:
                    cluster_key = f"{etype}::{doc_id}::{cname.lower()}"
                elif etype == "Requirement":
                    cluster_key = f"Requirement::{row['entity_id']}"
                elif etype in ["Authority", "Instrument", "EntityCategory", "Role", "Organization"]:
                    cluster_key = f"{etype}::{cname.lower()}"
                else:
                    cluster_key = f"{etype}::{doc_id}::{cname.lower()}"

                if cluster_key not in clusters:
                    clusters[cluster_key] = {
                        "canonical_name": cname,
                        "entity_type": etype,
                        "identity_scope": scope,
                        "description": desc or f"{etype}: {cname}",
                        "mentions": set(),
                        "chunks": set(),
                        "count": 0,
                    }

                c_info = clusters[cluster_key]
                c_info["count"] += 1
                if len(c_info["mentions"]) < 20 and mention:
                    c_info["mentions"].add(mention)
                if len(c_info["chunks"]) < 50:
                    c_info["chunks"].add(cid)

        print(f"Grouped {total_mentions} entity mentions into {len(clusters)} distinct canonical clusters.", flush=True)

        # Pass 2: Batch insert resolved entities into SQLite
        print("Persisting resolved clusters to database...", flush=True)
        with get_db_connection(self.store.db_path) as conn:
            conn.execute("DELETE FROM entity_resolutions;")

            batch = []
            for cluster_key, c_info in clusters.items():
                canonical_id = f"CANONICAL::{cluster_key}"
                batch.append((
                    canonical_id,
                    c_info["canonical_name"],
                    c_info["entity_type"],
                    c_info["identity_scope"],
                    c_info["description"],
                    c_info["count"],
                    json.dumps(list(c_info["chunks"])),
                    json.dumps(list(c_info["mentions"])),
                ))

                if len(batch) >= 5000:
                    conn.executemany("""
                    INSERT INTO entity_resolutions (
                        canonical_entity_id, canonical_name, entity_type, identity_scope,
                        consolidated_description, mention_count, chunk_ids_json, aliases_json, review_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unreviewed');
                    """, batch)
                    batch = []

            if batch:
                conn.executemany("""
                INSERT INTO entity_resolutions (
                    canonical_entity_id, canonical_name, entity_type, identity_scope,
                    consolidated_description, mention_count, chunk_ids_json, aliases_json, review_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unreviewed');
                """, batch)

            conn.commit()

        print(f"Conservative entity resolution completed. {len(clusters)} resolved entities saved.", flush=True)
        return {
            "total_mentions": total_mentions,
            "resolved_entities": len(clusters),
        }
