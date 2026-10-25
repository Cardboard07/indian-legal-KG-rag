"""Offline regression tests for evidence and distributed refinement correctness."""
import asyncio
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from cluster.cluster_dispatcher import MultiNodeLLMDispatcher, LLMNode
from scripts.run_graph_refinement import build_chunk_prompt, evaluate_batch
from scripts.reconcile_refinement_results import locate_quote_offsets, reconcile_result_file
from src.refinement_contract import validate_evaluation, validate_proposal


TEXT = "Banks shall file returns within 30 days unless exempt."
TRIPLET = {"relationship_id": "C::R1", "local_relationship_id": "R1", "numeric_constraints_json": [{"value": 30}]}
PROPOSAL = {"subject": "Banks", "subject_type": "EntityCategory", "predicate": "REQUIRES",
    "object": "File returns", "object_type": "Requirement", "description": TEXT, "exact_quote": TEXT,
    "conditions": [], "exceptions": ["unless exempt"], "numeric_constraints": [], "temporal_qualifiers": []}


def response():
    return {"audited_triplets": [{"relationship_id": "C::R1", "is_valid": True,
        "critique": "Supported", "error_type": "none"}], "missing_triplets": []}


class RefinementTests(unittest.TestCase):
    def test_prompt_retains_all_qualifiers_and_identity(self):
        p = json.loads(build_chunk_prompt({"chunk_id": "C", "raw_text": TEXT}, [TRIPLET]))
        self.assertEqual(p["existing_triplets"][0], TRIPLET)
        self.assertIn("ISSUES", p["schema"]["endpoint_rules"])
        self.assertIn("EntityCategory", p["schema"]["entity_types"])

    def test_audit_must_cover_all_ids_once(self):
        for audits in [[], response()["audited_triplets"] * 2,
                       [dict(response()["audited_triplets"][0], relationship_id="R2")]]:
            with self.assertRaises(ValueError):
                validate_evaluation({"audited_triplets": audits, "missing_triplets": []}, [TRIPLET], TEXT)

    def test_string_boolean_rejected(self):
        d = response(); d["audited_triplets"][0]["is_valid"] = "false"
        with self.assertRaises(ValueError):
            validate_evaluation(d, [TRIPLET], TEXT)

    def test_needs_context_is_distinct_from_invalid(self):
        d = response(); d["audited_triplets"][0].update(is_valid=None, error_type="needs_context")
        validate_evaluation(d, [TRIPLET], TEXT)

    def test_invented_quote_suffix_rejected(self):
        p = dict(PROPOSAL, exact_quote=TEXT[:42] + " invented legal rule")
        with self.assertRaises(ValueError):
            validate_proposal(p, TEXT)
        self.assertEqual(locate_quote_offsets(TEXT, p["exact_quote"]), (-1, -1))

    def test_endpoint_direction_enforced(self):
        with self.assertRaises(ValueError):
            validate_proposal(dict(PROPOSAL, subject_type="Organization"), TEXT)
        validate_proposal(PROPOSAL, TEXT)

    def test_qualifier_schema_enforced(self):
        with self.assertRaises(ValueError):
            validate_proposal(dict(PROPOSAL, conditions=["if applicable"]), TEXT)

    def test_reconciliation_refuses_mock_results(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/"results.json"
            p.write_text(json.dumps({"batch_id": "test", "execution_mode": "dry_run"}))
            with self.assertRaises(ValueError):
                reconcile_result_file(p, apply_changes=True)

    def test_explicit_url_default_port(self):
        d = MultiNodeLLMDispatcher(explicit_urls=["http://localhost", "https://localhost"])
        self.assertEqual([n.port for n in d.discover_nodes()], [80, 443])

    def test_dry_run_cannot_skip_live_inference(self):
        class FakeDispatcher(MultiNodeLLMDispatcher):
            calls = 0
            async def chat_completion(self, **kwargs):
                self.calls += 1
                return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(response())}}]}
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/"batch.json"
            p.write_text(json.dumps({"batch_id": "test", "chunks": [{"chunk_id": "C", "raw_text": TEXT}],
                                    "triplets_by_chunk": {"C": [TRIPLET]}}))
            d = FakeDispatcher(registry_dir=directory)
            asyncio.run(evaluate_batch(p, d, dry_run=True))
            result = asyncio.run(evaluate_batch(p, d))
            self.assertEqual(d.calls, 1)
            self.assertEqual(result["results"][0]["status"], "success")
            asyncio.run(evaluate_batch(p, d))
            self.assertEqual(d.calls, 1)
            batch = json.loads(p.read_text()); batch["chunks"][0]["raw_text"] += " Changed."
            p.write_text(json.dumps(batch)); asyncio.run(evaluate_batch(p, d))
            self.assertEqual(d.calls, 2)

    def test_active_request_count_decrements_exactly_once(self):
        async def check():
            d = MultiNodeLLMDispatcher()
            n = LLMNode({"url": "http://test:8080"}); n.is_healthy = True; n.active_requests = 1
            d.nodes[n.url] = n
            with patch("cluster.cluster_dispatcher.aiohttp", None), patch.object(d, "_chat_completion_urllib", return_value={}):
                await d.chat_completion([])
            self.assertEqual(n.active_requests, 1)
        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
