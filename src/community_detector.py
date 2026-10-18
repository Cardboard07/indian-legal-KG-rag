"""Hierarchical Leiden Community Detector for Legal GraphRAG Stage 3.
Implements recursive hierarchical Leiden partitioning matching Microsoft GraphRAG principles:
- Multi-level community hierarchy (Level 0 Root -> Level 1 Intermediate -> Level 2 Fine-grained)
- Objective: ModularityVertexPartition with fixed random seed for reproducibility
- Full coverage: Accounts for all connected components and isolated entities
- Strict validation: Acyclic tree, child subset containment, non-overlapping siblings
- Frontier extraction: Carries forward early-terminated branches so frontier covers 100% of graph
"""

import os
import sys
import json
import time
from typing import Dict, Any, List, Set, Optional, Tuple
from collections import defaultdict
import igraph as ig
import leidenalg

sys.path.insert(0, os.path.abspath("."))
from src.clustering_projection import ClusteringProjection

LEIDEN_RANDOM_SEED = 42
DEFAULT_MAX_LEVEL = 2
DEFAULT_MIN_COMMUNITY_SIZE = 15


class Community:
    """Represents a community in the hierarchical tree."""
    def __init__(
        self,
        community_id: str,
        level: int,
        parent_id: Optional[str] = None,
        member_entity_ids: Optional[Set[str]] = None,
        is_leaf: bool = False,
        termination_reason: Optional[str] = None,
    ):
        self.id = community_id
        self.level = level
        self.parent_id = parent_id
        self.children_ids: List[str] = []
        self.member_entity_ids: Set[str] = member_entity_ids or set()
        self.is_leaf = is_leaf
        self.termination_reason = termination_reason

        # Graph statistics
        self.internal_edge_count: int = 0
        self.boundary_edge_count: int = 0
        self.associated_chunk_ids: Set[str] = set()
        self.title: str = ""
        self.summary: str = ""
        self.representative_entities: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "level": self.level,
            "parent_id": self.parent_id,
            "children_ids": self.children_ids,
            "size": len(self.member_entity_ids),
            "member_entity_ids": sorted(list(self.member_entity_ids)),
            "is_leaf": self.is_leaf,
            "termination_reason": self.termination_reason,
            "internal_edge_count": self.internal_edge_count,
            "boundary_edge_count": self.boundary_edge_count,
            "associated_chunk_count": len(self.associated_chunk_ids),
            "title": self.title,
            "summary": self.summary,
            "representative_entities": self.representative_entities,
        }


class HierarchicalCommunityDetector:
    """Orchestrates recursive hierarchical Leiden community detection."""

    def __init__(
        self,
        projection: ClusteringProjection,
        snapshot_id: str = "v1",
        max_level: int = DEFAULT_MAX_LEVEL,
        min_community_size: int = DEFAULT_MIN_COMMUNITY_SIZE,
        seed: int = LEIDEN_RANDOM_SEED,
    ):
        self.projection = projection
        self.snapshot_id = snapshot_id
        self.max_level = max_level
        self.min_community_size = min_community_size
        self.seed = seed

        self.communities: Dict[str, Community] = {}
        self.level_counts: Dict[int, int] = {lvl: 0 for lvl in range(max_level + 1)}

    def detect_communities(self) -> Dict[str, Any]:
        """Runs the hierarchical Leiden community detection process."""
        t0 = time.time()
        print(f"\n--- Starting Hierarchical Leiden Detection (snapshot={self.snapshot_id}, seed={self.seed}) ---")

        # 1. Convert projection to igraph
        g, connected_nodes, node_map = self.projection.to_igraph()
        print(f"Graph ready for clustering: {g.vcount()} vertices, {g.ecount()} edges.")

        if g.vcount() == 0:
            print("Warning: Empty graph projection. Registering isolates only.")
            self._register_isolates()
            return {"total_communities": len(self.communities), "levels": self.level_counts}

        # 2. Run Top-Level Partitioning (Level 0)
        part = leidenalg.find_partition(
            g,
            leidenalg.ModularityVertexPartition,
            weights="weight",
            seed=self.seed,
        )

        print(f"Top-level partition identified {len(part)} base clusters.")

        # Instantiate Level 0 Communities
        l0_comm_list = []
        for cluster_idx, vertex_indices in enumerate(part):
            member_ids = {connected_nodes[v_idx] for v_idx in vertex_indices}
            comm_id = f"COMM::{self.snapshot_id}::L0::{cluster_idx}"
            comm = Community(
                community_id=comm_id,
                level=0,
                parent_id=None,
                member_entity_ids=member_ids,
            )
            self.communities[comm_id] = comm
            self.level_counts[0] += 1
            l0_comm_list.append(comm)

        # 3. Recursively Subdivide Communities up to max_level
        for comm in l0_comm_list:
            self._recursive_subdivide(comm, g, node_map, current_level=0)

        # 4. Register Isolated Entities
        self._register_isolates()

        # 5. Compute graph statistics & representative entities for each community
        self._compute_community_metadata()

        # 6. Validate tree integrity
        self._validate_hierarchy(connected_nodes)

        elapsed = time.time() - t0
        print(f"Hierarchical community detection complete in {elapsed:.2f}s:")
        for lvl in range(self.max_level + 1):
            print(f"  Level {lvl}: {self.level_counts.get(lvl, 0)} communities")
        print(f"  Total Communities: {len(self.communities)}")

        return {
            "total_communities": len(self.communities),
            "level_counts": self.level_counts,
            "elapsed_seconds": round(elapsed, 2),
        }

    def _recursive_subdivide(
        self,
        parent_comm: Community,
        full_graph: ig.Graph,
        node_map: Dict[str, int],
        current_level: int,
    ):
        """Recursively partitions a parent community into children at current_level + 1."""
        target_level = current_level + 1
        if target_level > self.max_level:
            parent_comm.is_leaf = True
            parent_comm.termination_reason = "max_level_reached"
            return

        if len(parent_comm.member_entity_ids) < self.min_community_size:
            parent_comm.is_leaf = True
            parent_comm.termination_reason = "size_below_threshold"
            return

        # Induce subgraph for parent community
        sub_indices = [node_map[eid] for eid in parent_comm.member_entity_ids if eid in node_map]
        subg = full_graph.subgraph(sub_indices)

        if subg.ecount() == 0:
            parent_comm.is_leaf = True
            parent_comm.termination_reason = "edgeless_subgraph"
            return

        # Run Leiden on Induced Subgraph
        try:
            sub_part = leidenalg.find_partition(
                subg,
                leidenalg.ModularityVertexPartition,
                weights="weight",
                seed=self.seed + target_level,
            )
        except Exception as ex:
            print(f"Warning: Sub-partition failed for {parent_comm.id}: {ex}")
            parent_comm.is_leaf = True
            parent_comm.termination_reason = f"partition_error_{ex}"
            return

        # If sub-partition did not split (1 community returned), terminate
        if len(sub_part) <= 1:
            parent_comm.is_leaf = True
            parent_comm.termination_reason = "unsplittable_community"
            return

        # Create Child Communities
        parent_comm.is_leaf = False
        sub_names = subg.vs["name"]

        for child_sub_idx, v_idxs in enumerate(sub_part):
            child_members = {sub_names[idx] for idx in v_idxs}
            child_idx = self.level_counts[target_level]
            child_id = f"COMM::{self.snapshot_id}::L{target_level}::{child_idx}"

            child_comm = Community(
                community_id=child_id,
                level=target_level,
                parent_id=parent_comm.id,
                member_entity_ids=child_members,
            )
            self.communities[child_id] = child_comm
            self.level_counts[target_level] += 1
            parent_comm.children_ids.append(child_id)

            # Recurse further
            self._recursive_subdivide(child_comm, full_graph, node_map, current_level=target_level)

    def _register_isolates(self):
        """Creates a dedicated community for isolated entities to preserve 100% entity coverage."""
        if not self.projection.isolates:
            return

        isolates_id = f"COMM::{self.snapshot_id}::ISOLATES"
        comm = Community(
            community_id=isolates_id,
            level=0,
            parent_id=None,
            member_entity_ids=set(self.projection.isolates),
            is_leaf=True,
            termination_reason="isolated_entities",
        )
        comm.title = "Isolated Regulatory Entities (Unconnected to Substantive Rules)"
        comm.summary = (
            f"Collection of {len(self.projection.isolates)} entities extracted from the corpus that lack "
            "substantive relational assertions in the primary regulatory graph."
        )
        self.communities[isolates_id] = comm

    def _compute_community_metadata(self):
        """Computes boundary/internal edge stats and representative entity labels."""
        edge_dict = self.projection.edges
        entities_dict = self.projection.entities

        # Prebuild entity adjacency index: node -> list of (neighbor, edge_data)
        adj = defaultdict(list)
        for (u, v), edge_data in edge_dict.items():
            adj[u].append((v, edge_data))
            adj[v].append((u, edge_data))

        for cid, comm in self.communities.items():
            lvl = comm.level
            member_set = comm.member_entity_ids
            internal_edges = 0
            boundary_edges = 0
            chunks = set()

            # Rank representative entities by internal degree within the community
            degree_in_comm = defaultdict(int)

            for eid in member_set:
                for other, edge_data in adj.get(eid, []):
                    chunks.update(edge_data["supporting_chunk_ids"])
                    if other in member_set:
                        internal_edges += 1
                        degree_in_comm[eid] += 1
                    else:
                        boundary_edges += 1

            comm.internal_edge_count = internal_edges // 2
            comm.boundary_edge_count = boundary_edges
            comm.associated_chunk_ids = chunks

            # Top 5 representative entities
            sorted_reps = sorted(
                list(member_set),
                key=lambda e: (degree_in_comm[e], entities_dict.get(e, {}).get("degree", 0)),
                reverse=True
            )
            comm.representative_entities = [entities_dict.get(e, {}).get("name", e) for e in sorted_reps[:5]]
            if not comm.title:
                rep_names = ", ".join(comm.representative_entities[:3])
                comm.title = f"Community L{comm.level} [{rep_names}]"

    def _validate_hierarchy(self, connected_nodes: List[str]):
        """Runs strict structural validation on the community tree."""
        # 1. Check parent-child membership containment
        for cid, comm in self.communities.items():
            if comm.children_ids:
                reconstructed = set()
                for ch_id in comm.children_ids:
                    ch_comm = self.communities[ch_id]
                    self.assertEqual(ch_comm.parent_id, comm.id, f"Child {ch_id} has wrong parent_id")
                    self.assertTrue(
                        ch_comm.member_entity_ids.issubset(comm.member_entity_ids),
                        f"Child {ch_id} members not subset of parent {comm.id}"
                    )
                    reconstructed.update(ch_comm.member_entity_ids)
                self.assertEqual(
                    reconstructed, comm.member_entity_ids,
                    f"Children of {comm.id} do not partition parent membership exactly"
                )

        # 2. Check non-overlapping siblings at each level
        for lvl in range(self.max_level + 1):
            comms_at_lvl = [c for c in self.communities.values() if c.level == lvl and not c.id.endswith("ISOLATES")]
            seen = set()
            for c in comms_at_lvl:
                overlap = seen.intersection(c.member_entity_ids)
                self.assertEqual(len(overlap), 0, f"Overlapping siblings at level {lvl}: {len(overlap)} nodes")
                seen.update(c.member_entity_ids)

        # 3. Check 100% connected node coverage at Level 0
        l0_nodes = set()
        for c in self.communities.values():
            if c.level == 0:
                l0_nodes.update(c.member_entity_ids)
        for nid in connected_nodes:
            self.assertIn(nid, l0_nodes, f"Connected node {nid} missing from Level 0")

    def assertEqual(self, a, b, msg):
        if a != b:
            raise AssertionError(f"{msg}: {a} != {b}")

    def assertTrue(self, expr, msg):
        if not expr:
            raise AssertionError(msg)

    def assertIn(self, item, container, msg):
        if item not in container:
            raise AssertionError(f"{msg}: {item} not in container")

    def get_frontier(self, target_level: int = 1) -> List[Community]:
        """Returns non-overlapping frontier communities covering 100% of the graph.
        Carries forward branches that terminated before target_level.
        """
        frontier = []
        for comm in self.communities.values():
            if comm.id.endswith("ISOLATES"):
                continue

            if comm.level == target_level:
                frontier.append(comm)
            elif comm.level < target_level and comm.is_leaf:
                # Early terminated branch: carry forward
                frontier.append(comm)

        return frontier

    def export_communities_json(self, filepath: str = "data/community_reports/communities.json"):
        """Exports all communities to a durable JSON file."""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        data = {cid: comm.to_dict() for cid, comm in self.communities.items()}
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Exported {len(self.communities)} communities to {filepath}")


if __name__ == "__main__":
    proj = ClusteringProjection()
    proj.build_projection()
    detector = HierarchicalCommunityDetector(projection=proj, max_level=2)
    res = detector.detect_communities()
    detector.export_communities_json()

    # Test frontier
    for lvl in [0, 1, 2]:
        frontier = detector.get_frontier(target_level=lvl)
        total_nodes = sum(len(c.member_entity_ids) for c in frontier)
        print(f"Frontier at Level {lvl}: {len(frontier)} communities covering {total_nodes} connected entities.")
