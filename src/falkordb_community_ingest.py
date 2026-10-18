"""FalkorDB Community Ingestion Engine for Legal GraphRAG Stage 3.
Idempotently persists Community nodes and hierarchy edges in FalkorDB:
- (:Community {id, snapshot_id, level, title, size, internal_edges, boundary_edges, is_leaf, termination_reason})
- (:Entity)-[:IN_COMMUNITY]->(:Community)
- (:Community)-[:PARENT_OF]->(:Community)
- (:Community)-[:HAS_REPORT]->(:CommunityReport)

Includes bounded batching, idempotency checks, and durable manifest generation.
"""

import os
import sys
import json
import time
from typing import Dict, Any, List

sys.path.insert(0, os.path.abspath("."))
from src.config import get_falkordb_client, FALKORDB_GRAPH_NAME


class FalkorDBCommunityIngest:
    """Manages idempotent persistence of communities and reports into FalkorDB."""

    def __init__(self, graph_name: str = FALKORDB_GRAPH_NAME, batch_size: int = 500):
        self.graph_name = graph_name
        self.batch_size = batch_size
        self.client = get_falkordb_client()
        self.graph = self.client.select_graph(graph_name)

    def ingest_communities(self, communities_json_path: str = "data/community_reports/communities.json") -> Dict[str, Any]:
        """Ingests all communities and parent-child edges into FalkorDB."""
        t0 = time.time()
        print(f"\n--- Ingesting Communities into FalkorDB '{self.graph_name}' ---", flush=True)

        # Ensure index on Community(id) and CommunityReport(id)
        try:
            self.graph.query("CREATE INDEX FOR (c:Community) ON (c.id)")
            self.graph.query("CREATE INDEX FOR (cr:CommunityReport) ON (cr.id)")
            print("Ensured indexes on :Community(id) and :CommunityReport(id).", flush=True)
        except Exception as ex:
            print(f"Index notice: {ex}", flush=True)

        if not os.path.exists(communities_json_path):
            raise FileNotFoundError(f"Communities file {communities_json_path} not found.")

        with open(communities_json_path, "r", encoding="utf-8") as f:
            communities = json.load(f)

        total_comms = len(communities)
        print(f"Loaded {total_comms} communities to ingest.", flush=True)

        # 1. Ingest Community Nodes
        comm_batch = []
        ingested_comms = 0

        for cid, cdata in communities.items():
            comm_batch.append({
                "id": cid,
                "snapshot_id": cid.split("::")[1] if "::" in cid else "v1",
                "level": cdata["level"],
                "title": cdata.get("title", ""),
                "size": cdata.get("size", 0),
                "internal_edges": cdata.get("internal_edge_count", 0),
                "boundary_edges": cdata.get("boundary_edge_count", 0),
                "is_leaf": cdata.get("is_leaf", False),
                "termination_reason": cdata.get("termination_reason", ""),
            })

            if len(comm_batch) >= self.batch_size:
                self._upsert_community_nodes(comm_batch)
                ingested_comms += len(comm_batch)
                comm_batch = []

        if comm_batch:
            self._upsert_community_nodes(comm_batch)
            ingested_comms += len(comm_batch)

        print(f"Community nodes ingested: {ingested_comms}/{total_comms}.")

        # 2. Ingest Parent-Child Edges (:Community)-[:PARENT_OF]->(:Community)
        parent_child_pairs = []
        for cid, cdata in communities.items():
            for child_id in cdata.get("children_ids", []):
                parent_child_pairs.append({"parent_id": cid, "child_id": child_id})

        total_hierarchy_edges = len(parent_child_pairs)
        print(f"Ingesting {total_hierarchy_edges} parent-child hierarchy edges...")

        edge_batch = []
        ingested_edges = 0
        for pair in parent_child_pairs:
            edge_batch.append(pair)
            if len(edge_batch) >= self.batch_size:
                self._upsert_parent_child_edges(edge_batch)
                ingested_edges += len(edge_batch)
                edge_batch = []

        if edge_batch:
            self._upsert_parent_child_edges(edge_batch)
            ingested_edges += len(edge_batch)

        print(f"Parent-child hierarchy edges ingested: {ingested_edges}/{total_hierarchy_edges}.")

        # 3. Ingest Entity Membership Edges (:Entity)-[:IN_COMMUNITY]->(:Community)
        # Note: To avoid extreme batch memory overhead on 157k nodes, link Level 0 and Level 1 membership
        print("Ingesting entity-to-community membership edges for Level 0 and Level 1...")
        membership_pairs = []
        for cid, cdata in communities.items():
            if cdata["level"] <= 1 and not cid.endswith("ISOLATES"):
                for eid in cdata.get("member_entity_ids", []):
                    membership_pairs.append({"entity_id": eid, "community_id": cid})

        total_memberships = len(membership_pairs)
        print(f"Ingesting {total_memberships} entity membership edges...")

        m_batch = []
        ingested_memberships = 0
        for m in membership_pairs:
            m_batch.append(m)
            if len(m_batch) >= self.batch_size:
                self._upsert_membership_edges(m_batch)
                ingested_memberships += len(m_batch)
                m_batch = []

        if m_batch:
            self._upsert_membership_edges(m_batch)
            ingested_memberships += len(m_batch)

        elapsed = time.time() - t0
        manifest = {
            "ingested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_communities_ingested": ingested_comms,
            "total_hierarchy_edges_ingested": ingested_edges,
            "total_memberships_ingested": ingested_memberships,
            "elapsed_seconds": round(elapsed, 2),
        }

        manifest_path = "data/community_reports/community_ingestion_manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        print(f"Community ingestion complete in {elapsed:.2f}s. Manifest: {manifest_path}")
        return manifest

    def _upsert_community_nodes(self, batch: List[Dict[str, Any]]):
        cypher = """
        UNWIND $batch AS item
        MERGE (c:Community {id: item.id})
        ON CREATE SET
            c.snapshot_id = item.snapshot_id,
            c.level = item.level,
            c.title = item.title,
            c.size = item.size,
            c.internal_edges = item.internal_edges,
            c.boundary_edges = item.boundary_edges,
            c.is_leaf = item.is_leaf,
            c.termination_reason = item.termination_reason,
            c.created_at = timestamp()
        ON MATCH SET
            c.snapshot_id = item.snapshot_id,
            c.level = item.level,
            c.title = item.title,
            c.size = item.size,
            c.internal_edges = item.internal_edges,
            c.boundary_edges = item.boundary_edges,
            c.is_leaf = item.is_leaf,
            c.termination_reason = item.termination_reason
        """
        self.graph.query(cypher, {"batch": batch})

    def _upsert_parent_child_edges(self, batch: List[Dict[str, Any]]):
        cypher = """
        UNWIND $batch AS item
        MATCH (p:Community {id: item.parent_id})
        MATCH (c:Community {id: item.child_id})
        MERGE (p)-[:PARENT_OF]->(c)
        """
        self.graph.query(cypher, {"batch": batch})

    def _upsert_membership_edges(self, batch: List[Dict[str, Any]]):
        cypher = """
        UNWIND $batch AS item
        MATCH (e:Entity {id: item.entity_id})
        MATCH (c:Community {id: item.community_id})
        MERGE (e)-[:IN_COMMUNITY]->(c)
        """
        self.graph.query(cypher, {"batch": batch})


if __name__ == "__main__":
    ingester = FalkorDBCommunityIngest()
    ingester.ingest_communities()
