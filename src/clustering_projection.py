"""Clustering Projection Builder for Legal GraphRAG Stage 3.
Extracts an explicit, deduplicated entity-to-entity weighted undirected projection
from FalkorDB for hierarchical Leiden community detection.

Key Design Principles:
- Maps 4-layer assertion structures to endpoint relationships (s <-> t)
- Excludes storage bookkeeping, evidence-span, and chunk-containment edges
- Hub suppression: Excludes administrative ISSUES edges by default to prevent star-collapse
- Parallel edge aggregation with duplicate-derived weight = count of unique supporting chunks
- Self-loop removal and isolate tracking ensuring complete coverage
- Retains complete back-references to source assertions and chunk evidence
"""

import os
import sys
import json
import time
from typing import Dict, Any, List, Tuple, Set, Optional
from collections import defaultdict

sys.path.insert(0, os.path.abspath("."))
from src.config import get_falkordb_client, FALKORDB_GRAPH_NAME

# Substantive regulatory relationship types for semantic clustering
DEFAULT_SUBSTANTIVE_RELATIONSHIPS = [
    "APPLIES_TO",
    "REQUIRES",
    "PERMITS",
    "PROHIBITS",
    "DEFINES",
    "REFERS_TO",
    "HAS_PROVISION",
]

# Administrative super-nodes that cause star-cluster distortion
DEFAULT_ADMIN_HUB_ENTITIES = {
    "canonical::authority::reserve bank of india",
}


class ClusteringProjection:
    """Manages the entity-to-entity clustering projection with evidence traceability."""

    def __init__(
        self,
        graph_name: str = FALKORDB_GRAPH_NAME,
        allowed_relationships: Optional[List[str]] = None,
        suppress_admin_hubs: bool = True,
    ):
        self.graph_name = graph_name
        self.allowed_relationships = allowed_relationships or DEFAULT_SUBSTANTIVE_RELATIONSHIPS
        self.suppress_admin_hubs = suppress_admin_hubs

        self.client = get_falkordb_client()
        self.graph = self.client.select_graph(graph_name)

        # In-memory projection structures
        self.entities: Dict[str, Dict[str, Any]] = {}  # id -> {name, type, degree}
        self.edges: Dict[Tuple[str, str], Dict[str, Any]] = {}  # (u, v) -> {weight, assertions, rels, chunks}
        self.isolates: Set[str] = set()

    def build_projection(self) -> Dict[str, Any]:
        """Queries FalkorDB and builds the weighted undirected projection."""
        t0 = time.time()
        print(f"Building clustering projection from FalkorDB graph '{self.graph_name}'...")
        print(f"  Allowed relationship types: {self.allowed_relationships}")
        print(f"  Suppress admin hubs: {self.suppress_admin_hubs}")

        # 1. Fetch all eligible Entities
        cypher_entities = """
        MATCH (e:Entity)
        RETURN e.id, e.canonical_name, e.entity_type, e.identity_scope
        """
        res_e = self.graph.query(cypher_entities)
        for row in res_e.result_set:
            eid, name, etype, scope = row[0], row[1], row[2], row[3]
            self.entities[eid] = {
                "id": eid,
                "name": name or eid,
                "type": etype or "Unknown",
                "scope": scope or "global",
                "degree": 0,
            }

        total_entities_count = len(self.entities)
        print(f"Loaded {total_entities_count} total entities.")

        # 2. Fetch substantive relationships and assertions
        cypher_rels = """
        MATCH (s:Entity)-[r:RELATION]->(t:Entity)
        WHERE r.rel_type IN $allowed_rels
        RETURN s.id, r.rel_type, t.id, r.assertion_id, r.is_conditional
        """
        res_r = self.graph.query(cypher_rels, {"allowed_rels": self.allowed_relationships})

        # Fetch assertion metadata (chunk_id) to calculate deduplicated passage weights
        cypher_assertions = """
        MATCH (a:RelationshipAssertion)
        RETURN a.id, a.chunk_id
        """
        res_a = self.graph.query(cypher_assertions)
        assertion_to_chunk = {row[0]: row[1] for row in res_a.result_set if row[1]}

        raw_edges_processed = 0
        self_loops_count = 0
        hubs_filtered_count = 0

        # Group parallel edges between entity pairs
        pair_data = defaultdict(lambda: {
            "assertion_ids": set(),
            "relationship_types": set(),
            "chunk_ids": set(),
            "is_conditional": False,
        })

        for row in res_r.result_set:
            s_id, r_type, t_id, a_id, is_cond = row[0], row[1], row[2], row[3], bool(row[4])
            raw_edges_processed += 1

            # Check self-loops
            if s_id == t_id:
                self_loops_count += 1
                continue

            # Check administrative hubs
            if self.suppress_admin_hubs:
                if s_id.lower() in DEFAULT_ADMIN_HUB_ENTITIES or t_id.lower() in DEFAULT_ADMIN_HUB_ENTITIES:
                    hubs_filtered_count += 1
                    continue

            # Ensure canonical undirected ordering (u < v)
            u, v = (s_id, t_id) if s_id < t_id else (t_id, s_id)

            if a_id:
                pair_data[(u, v)]["assertion_ids"].add(a_id)
                cid = assertion_to_chunk.get(a_id)
                if cid:
                    pair_data[(u, v)]["chunk_ids"].add(cid)
            pair_data[(u, v)]["relationship_types"].add(r_type)
            if is_cond:
                pair_data[(u, v)]["is_conditional"] = True

        # 3. Construct aggregated projection edges with deduplicated passage weights
        connected_entities = set()
        for (u, v), data in pair_data.items():
            # Weight = number of unique supporting chunks (min 1.0)
            weight = float(max(len(data["chunk_ids"]), 1))

            self.edges[(u, v)] = {
                "source": u,
                "target": v,
                "weight": weight,
                "assertion_ids": sorted(list(data["assertion_ids"])),
                "relationship_types": sorted(list(data["relationship_types"])),
                "supporting_chunk_ids": sorted(list(data["chunk_ids"])),
                "is_conditional": data["is_conditional"],
            }

            self.entities[u]["degree"] += 1
            self.entities[v]["degree"] += 1
            connected_entities.add(u)
            connected_entities.add(v)

        # 4. Identify isolates
        self.isolates = set(self.entities.keys()) - connected_entities

        elapsed = time.time() - t0
        stats = {
            "total_entities": len(self.entities),
            "connected_entities": len(connected_entities),
            "isolated_entities": len(self.isolates),
            "raw_edges_queried": raw_edges_processed,
            "self_loops_excluded": self_loops_count,
            "admin_hub_edges_filtered": hubs_filtered_count,
            "projected_undirected_edges": len(self.edges),
            "avg_degree_connected": round(sum(self.entities[e]["degree"] for e in connected_entities) / max(len(connected_entities), 1), 2),
            "build_latency_seconds": round(elapsed, 2),
        }

        print(f"Projection built in {elapsed:.2f}s:")
        print(f"  Connected entities: {stats['connected_entities']}")
        print(f"  Projected edges: {stats['projected_undirected_edges']}")
        print(f"  Isolated entities: {stats['isolated_entities']}")
        print(f"  Self-loops removed: {stats['self_loops_excluded']}")
        print(f"  Admin hub edges filtered: {stats['admin_hub_edges_filtered']}")

        return stats

    def to_igraph(self) -> Tuple[Any, List[str], Dict[str, int]]:
        """Converts the connected projection into an igraph.Graph for Leiden clustering."""
        import igraph as ig

        connected_nodes = [eid for eid, data in self.entities.items() if data["degree"] > 0]
        node_to_idx = {eid: idx for idx, eid in enumerate(connected_nodes)}

        edges_idx = []
        weights = []
        for (u, v), edge_data in self.edges.items():
            if u in node_to_idx and v in node_to_idx:
                edges_idx.append((node_to_idx[u], node_to_idx[v]))
                weights.append(edge_data["weight"])

        g = ig.Graph(n=len(connected_nodes), edges=edges_idx, directed=False)
        g.es["weight"] = weights
        g.vs["name"] = connected_nodes

        return g, connected_nodes, node_to_idx


if __name__ == "__main__":
    proj = ClusteringProjection()
    stats = proj.build_projection()
    g, nodes, node_map = proj.to_igraph()
    print(f"Constructed igraph: {g.vcount()} vertices, {g.ecount()} edges.")
