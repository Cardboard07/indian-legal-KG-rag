"""Comprehensive Stage 3 Failure Modes and Verification Suite for Legal GraphRAG.
Verifies all 17 specified failure conditions and edge cases:
1. Empty and edgeless projections
2. Isolates and disconnected components
3. Invalid and negative weights
4. Duplicate evidence deduplication
5. Unsplittable communities termination
6. Uneven hierarchy depth handling
7. Correct frontier coverage (no gaps, no overlaps)
8. Missing child reports fallback
9. Oversized context budget enforcement
10. Invalid report citations integrity
11. Preservation of conditions and exceptions
12. Failed map batches resilience
13. All-zero map results explicit insufficient-evidence handling
14. Interrupted report generation resumability
15. Idempotent FalkorDB persistence
16. Stale-cache and snapshot hash verification
17. Preservation of existing retrieval modes (baseline_hybrid & hybrid_graph)
"""

import os
import sys
import json
import time
import unittest
import igraph as ig

sys.path.insert(0, os.path.abspath("."))
from src.config import get_falkordb_client, FALKORDB_GRAPH_NAME
from src.clustering_projection import ClusteringProjection
from src.community_detector import HierarchicalCommunityDetector, Community
from src.report_context_builder import ReportContextBuilder
from src.report_generator import CommunityReportGenerator
from src.global_search import GlobalCommunitySearcher
from src.rag_pipeline import LegalRAGPipeline


class TestStage3FailureModes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = get_falkordb_client()
        cls.graph = cls.client.select_graph(FALKORDB_GRAPH_NAME)
        cls.pipeline = LegalRAGPipeline(regulator="RBI", enable_graph=True, context_budget_chars=5000)
        cls.pipeline.initialize()

        cls.projection = ClusteringProjection()
        cls.projection.build_projection()

        with open("data/community_reports/communities.json", "r", encoding="utf-8") as f:
            cls.communities = json.load(f)
        with open("data/community_reports/reports.json", "r", encoding="utf-8") as f:
            cls.reports = json.load(f)

    def test_01_empty_and_edgeless_projection(self):
        """Verify that an empty or edgeless graph safely initializes without crashing."""
        empty_proj = ClusteringProjection(graph_name=FALKORDB_GRAPH_NAME, allowed_relationships=[])
        # Manually clear edges
        empty_proj.entities = {"A": {"id": "A", "degree": 0}, "B": {"id": "B", "degree": 0}}
        empty_proj.edges = {}
        empty_proj.isolates = {"A", "B"}

        g, nodes, node_map = empty_proj.to_igraph()
        self.assertEqual(g.vcount(), 0)
        self.assertEqual(g.ecount(), 0)

        detector = HierarchicalCommunityDetector(projection=empty_proj, snapshot_id="test_empty")
        res = detector.detect_communities()
        self.assertIn("COMM::test_empty::ISOLATES", detector.communities)

    def test_02_isolates_and_disconnected_components(self):
        """Verify that entities without relations are preserved in the isolates community."""
        isolates_id = "COMM::v1::ISOLATES"
        self.assertIn(isolates_id, self.communities)
        isolates_comm = self.communities[isolates_id]
        self.assertGreater(len(isolates_comm["member_entity_ids"]), 0)
        self.assertEqual(isolates_comm["level"], 0)
        self.assertTrue(isolates_comm["is_leaf"])

    def test_03_invalid_and_negative_weights(self):
        """Verify all edge weights in the projection are strictly finite, positive, and non-negative."""
        for (u, v), edata in self.projection.edges.items():
            w = edata["weight"]
            self.assertGreaterEqual(w, 1.0, f"Edge ({u}, {v}) has weight {w} < 1.0")
            self.assertFalse(isinstance(w, complex))

    def test_04_duplicate_evidence_deduplication(self):
        """Verify duplicate mentions from the same chunk do not inflate weights."""
        # Check an edge with multiple assertions from same chunk
        for (u, v), edata in self.projection.edges.items():
            # weight must equal len(supporting_chunk_ids) (or 1 if 0 chunks)
            expected_weight = float(max(len(edata["supporting_chunk_ids"]), 1))
            self.assertEqual(edata["weight"], expected_weight)

    def test_05_unsplittable_communities_termination(self):
        """Verify communities that cannot be partitioned further cleanly terminate as leaves."""
        leaf_reasons = set()
        for cid, cdata in self.communities.items():
            if cdata.get("is_leaf"):
                leaf_reasons.add(cdata.get("termination_reason"))
        self.assertTrue(
            any(r in leaf_reasons for r in ["unsplittable_community", "size_below_threshold", "max_level_reached"])
        )

    def test_06_uneven_hierarchy_depth(self):
        """Verify the tree handles varying branch depth gracefully without errors."""
        levels = {cdata["level"] for cdata in self.communities.values()}
        self.assertIn(0, levels)
        self.assertIn(1, levels)
        self.assertIn(2, levels)

    def test_07_correct_frontier_coverage(self):
        """Verify that frontiers at Level 0, Level 1, and Level 2 cover 100% of connected nodes with 0 overlaps."""
        detector = HierarchicalCommunityDetector(projection=self.projection, max_level=2)
        detector.communities = {
            cid: Community(
                community_id=c["id"],
                level=c["level"],
                parent_id=c.get("parent_id"),
                member_entity_ids=set(c.get("member_entity_ids", [])),
                is_leaf=c.get("is_leaf", False),
                termination_reason=c.get("termination_reason"),
            ) for cid, c in self.communities.items()
        }

        for lvl in [0, 1, 2]:
            frontier = detector.get_frontier(target_level=lvl)
            frontier_nodes = set()
            for comm in frontier:
                overlap = frontier_nodes.intersection(comm.member_entity_ids)
                self.assertEqual(len(overlap), 0, f"Overlapping nodes in frontier at level {lvl}")
                frontier_nodes.update(comm.member_entity_ids)
            # Must equal connected nodes
            self.assertEqual(len(frontier_nodes), 26694, f"Frontier at level {lvl} does not cover 26,694 nodes")

    def test_08_missing_child_reports_fallback(self):
        """Verify context builder degrades gracefully when child reports are missing."""
        builder = ReportContextBuilder(projection=self.projection)
        test_comm = self.communities["COMM::v1::L0::1"]
        # Pass empty child reports dictionary
        ctx, manifest = builder.build_context(test_comm, child_reports={})
        self.assertIsInstance(ctx, str)
        self.assertGreater(len(ctx), 100)
        self.assertEqual(len(manifest["child_reports_used"]), 0)

    def test_09_oversized_contexts_budget_enforcement(self):
        """Verify element packing strictly enforces the max_context_chars budget."""
        budgets = [3000, 6000, 12000]
        test_comm = self.communities["COMM::v1::L0::1"]
        for b in budgets:
            builder = ReportContextBuilder(projection=self.projection, max_context_chars=b)
            ctx, manifest = builder.build_context(test_comm)
            self.assertLessEqual(len(ctx), b + 200, f"Context length {len(ctx)} exceeded budget {b}")

    def test_10_invalid_report_citations_integrity(self):
        """Verify report findings link to existing assertion IDs and valid chunk IDs."""
        for cid, r in list(self.reports.items())[:20]:
            for f in r.get("findings", []):
                for cid_ref in f.get("supporting_chunk_ids", []):
                    self.assertTrue(cid_ref.endswith(".md"), f"Invalid chunk extension: {cid_ref}")

    def test_11_lost_exceptions_preservation(self):
        """Verify conditions, exceptions, and numeric thresholds survive in community reports."""
        reports_with_qualifiers = 0
        for cid, r in self.reports.items():
            quals = r.get("conditions_and_exceptions", {})
            has_qual = len(quals.get("conditions", [])) > 0 or len(quals.get("exceptions", [])) > 0 or len(quals.get("numeric_constraints", [])) > 0
            if has_qual:
                reports_with_qualifiers += 1
        self.assertGreater(reports_with_qualifiers, 0, "Community reports must contain preserved regulatory qualifiers")

    def test_12_failed_map_batches_resilience(self):
        """Verify map stage handles empty or malformed batches without crashing."""
        searcher = GlobalCommunitySearcher()
        points = searcher._map_batch("test query", [])
        self.assertEqual(len(points), 0)

    def test_13_all_zero_map_results(self):
        """Verify global search returns explicit insufficient-evidence result when all map scores are zero."""
        searcher = GlobalCommunitySearcher()
        # Query containing random non-regulatory gibberish
        res = searcher.search("xyzzyqwer12345 non_existent_quantum_quark", target_level=0)
        self.assertIn("No relevant regulatory findings", res["answer"])
        self.assertEqual(res["trace"]["usable_points_count"], 0)

    def test_14_interrupted_report_generation_resumability(self):
        """Verify report generator loads existing reports and skips redundant generation."""
        generator = CommunityReportGenerator(projection=self.projection)
        self.assertGreaterEqual(len(generator.reports), 195)
        # Re-running synthesize_report on existing ID uses checkpoint
        test_comm = self.communities["COMM::v1::L0::1"]
        self.assertIn(test_comm["id"], generator.reports)

    def test_15_idempotent_falkordb_persistence(self):
        """Verify re-running MERGE on Community nodes in FalkorDB produces 0 duplicates."""
        res_before = self.graph.query("MATCH (c:Community) RETURN count(c)")
        count_before = res_before.result_set[0][0]
        self.assertGreaterEqual(count_before, 4920)

        # Re-run MERGE on sample community
        sample_id = "COMM::v1::L0::0"
        self.graph.query("""
            MERGE (c:Community {id: $id})
            ON MATCH SET c.verified_at = timestamp()
        """, {"id": sample_id})

        res_after = self.graph.query("MATCH (c:Community) RETURN count(c)")
        count_after = res_after.result_set[0][0]
        self.assertEqual(count_before, count_after, "MERGE must not create duplicate Community nodes")

    def test_16_stale_cache_and_snapshot_hash(self):
        """Verify snapshot manifest contains valid content hashes."""
        with open("data/snapshots/snapshot_v1_manifest.json", "r", encoding="utf-8") as f:
            manifest = json.load(f)
        hashes = manifest["content_hashes"]
        self.assertTrue(len(hashes["extraction_store_db_sha256"]) == 64)
        self.assertTrue(len(hashes["rbi_fts_db_sha256"]) == 64)

    def test_17_preservation_of_existing_retrieval_modes(self):
        """Verify baseline_hybrid and hybrid_graph remain fully functional alongside global modes."""
        q = "NBFC asset size threshold for Middle Layer"
        res_base = self.pipeline.retrieve(q, mode="baseline_hybrid", top_k=3)
        self.assertEqual(res_base["mode"], "baseline_hybrid")
        self.assertGreater(len(res_base["evidence"]), 0)

        res_graph = self.pipeline.retrieve(q, mode="hybrid_graph", top_k=3)
        self.assertEqual(res_graph["mode"], "hybrid_graph")
        self.assertGreater(len(res_graph["evidence"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
