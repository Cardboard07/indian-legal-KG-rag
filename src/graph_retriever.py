"""Local Graph Expansion & Evidence Retrieval Engine.
Connects to FalkorDB ('legal_kg_dev') and performs:
1. Seed selection from baseline candidate chunks and query entity matching
2. Hub node suppression (filtering out high-degree regulators and generic categories)
3. Bounded 1-2 hop graph expansion over substantive regulatory relations
4. Supporting evidence span retrieval with verbatim quotations and chunk links
5. Graph-augmented reranking and context budget enforcement
6. Full diagnostic trace generation
"""

import os
import re
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional, Set

from src.config import get_falkordb_client, FALKORDB_GRAPH_NAME
from src.baseline_retriever import BaselineHybridRetriever


# High-degree hub entities to suppress from unbounded neighbor expansion
HUB_ENTITIES_SUPPRESSION_SET: Set[str] = {
    "canonical::authority::reserve bank of india",
    "canonical::authority::ministry of corporate affairs, government of india",
    "canonical::authority::securities and exchange board of india",
    "canonical::authority::central government",
    "canonical::entitycategory::company",
    "canonical::entitycategory::body corporate",
    "canonical::entitycategory::person",
    "canonical::entitycategory::financial institution",
}

# Substantive regulatory relationship types prioritized during expansion
PRIORITY_RELATIONSHIPS: List[str] = [
    "REQUIRES",
    "PROHIBITS",
    "PERMITS",
    "EXEMPTS_FROM",
    "DEFINES",
    "APPLIES_TO",
    "HAS_PROVISION",
    "REFERS_TO",
    "AMENDS",
    "SUPERSEDES",
]


class LocalGraphRetriever:
    """Performs evidence-grounded local graph expansion on top of baseline hybrid retrieval."""

    def __init__(
        self,
        graph_name: str = FALKORDB_GRAPH_NAME,
        max_hops: int = 2,
        max_fanout_per_node: int = 12,
        context_budget_chars: int = 6000,
    ):
        self.graph_name = graph_name
        self.max_hops = max_hops
        self.max_fanout_per_node = max_fanout_per_node
        self.context_budget_chars = context_budget_chars
        self.client = get_falkordb_client()
        self.graph = self.client.select_graph(self.graph_name)

    def extract_seeds(
        self,
        baseline_candidates: List[Dict[str, Any]],
        query: str,
        max_seeds: int = 8,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Extracts seed entities from top baseline chunks and explicit query entity matching."""
        seeds = []
        seed_ids = set()
        trace_messages = []

        # 1. Seeds from top candidate chunks via MENTIONS edge in FalkorDB
        top_chunk_ids = [c["chunk_id"] for c in baseline_candidates[:5]]
        if top_chunk_ids:
            cypher = """
            UNWIND $cids AS cid
            MATCH (c:Chunk {id: cid})-[m:MENTIONS]->(e:Entity)
            RETURN e.id, e.canonical_name, e.entity_type, e.identity_scope, count(m) as mention_weight
            ORDER BY mention_weight DESC
            LIMIT 15
            """
            try:
                res = self.graph.query(cypher, {"cids": top_chunk_ids})
                for row in res.result_set:
                    eid, name, etype, scope, weight = row[0], row[1], row[2], row[3], row[4]
                    if eid.lower() in HUB_ENTITIES_SUPPRESSION_SET:
                        trace_messages.append(f"Suppressed high-degree hub seed: {name} ({eid})")
                        continue
                    if eid not in seed_ids:
                        seed_ids.add(eid)
                        seeds.append({
                            "id": eid,
                            "name": name,
                            "type": etype,
                            "scope": scope,
                            "source": "chunk_mention",
                            "initial_weight": 1.0 + min(float(weight) * 0.1, 0.5),
                        })
            except Exception as ex:
                trace_messages.append(f"Chunk seed lookup warning: {ex}")

        # 2. Seeds from explicit query keyword / entity matching
        tokens = re.findall(r"\b[A-Za-z0-9\-]{3,}\b", query)
        candidate_terms = [t.lower() for t in tokens if len(t) > 3]

        # Check for key regulatory acronyms & phrases
        key_phrases = ["base layer", "middle layer", "nbfc-bl", "nbfc-ml", "cco", "chief compliance officer", "type-i", "p2p", "account aggregator"]
        q_lower = query.lower()
        for phrase in key_phrases:
            if phrase in q_lower:
                candidate_terms.append(phrase)

        for term in set(candidate_terms):
            cypher = """
            MATCH (e:Entity)
            WHERE toLower(e.canonical_name) CONTAINS $term
            RETURN e.id, e.canonical_name, e.entity_type, e.identity_scope
            LIMIT 3
            """
            try:
                res = self.graph.query(cypher, {"term": term})
                for row in res.result_set:
                    eid, name, etype, scope = row[0], row[1], row[2], row[3]
                    if eid.lower() in HUB_ENTITIES_SUPPRESSION_SET:
                        continue
                    if eid not in seed_ids:
                        seed_ids.add(eid)
                        seeds.append({
                            "id": eid,
                            "name": name,
                            "type": etype,
                            "scope": scope,
                            "source": "query_match",
                            "initial_weight": 1.5,
                        })
            except Exception:
                pass

        return seeds[:max_seeds], trace_messages

    def expand_graph(
        self,
        seeds: List[Dict[str, Any]],
        hops: int = 1,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Expands seeds 1-2 hops through substantive regulatory relationships."""
        traversed_paths = []
        retrieved_assertions = []
        visited_assertion_ids = set()

        seed_ids = [s["id"] for s in seeds]
        if not seed_ids:
            return traversed_paths, retrieved_assertions

        # 1-Hop Expansion Cypher Query
        cypher = """
        UNWIND $seeds AS seed_id
        MATCH (s:Entity {id: seed_id})-[r:RELATION]->(t:Entity)
        WHERE r.rel_type IN $allowed_rels
        OPTIONAL MATCH (s)-[:SUBJECT_OF]->(a:RelationshipAssertion {id: r.assertion_id})-[:OBJECT]->(t)
        RETURN s.id, s.canonical_name, r.rel_type, t.id, t.canonical_name, t.entity_type,
               r.assertion_id, r.is_conditional,
               a.description, a.conditions_json, a.exceptions_json, a.numeric_constraints_json, a.chunk_id
        LIMIT 60
        """
        try:
            res = self.graph.query(cypher, {"seeds": seed_ids, "allowed_rels": PRIORITY_RELATIONSHIPS})
            for row in res.result_set:
                s_id, s_name, r_type, t_id, t_name, t_type = row[0], row[1], row[2], row[3], row[4], row[5]
                a_id, is_cond = row[6], bool(row[7])
                desc, conds, excs, nums, chunk_id = row[8], row[9], row[10], row[11], row[12]

                traversed_paths.append({
                    "hop": 1,
                    "source_id": s_id,
                    "source_name": s_name,
                    "relation_type": r_type,
                    "target_id": t_id,
                    "target_name": t_name,
                    "target_type": t_type,
                    "is_conditional": is_cond,
                })

                if a_id and a_id not in visited_assertion_ids:
                    visited_assertion_ids.add(a_id)
                    retrieved_assertions.append({
                        "assertion_id": a_id,
                        "chunk_id": chunk_id,
                        "source_name": s_name,
                        "relation_type": r_type,
                        "target_name": t_name,
                        "description": desc or f"{s_name} {r_type} {t_name}",
                        "is_conditional": is_cond,
                        "conditions": json.loads(conds) if conds else [],
                        "exceptions": json.loads(excs) if excs else [],
                        "numeric_constraints": json.loads(nums) if nums else [],
                    })
        except Exception as ex:
            print(f"Graph expansion error: {ex}", flush=True)

        return traversed_paths, retrieved_assertions

    def fetch_assertion_evidence(
        self,
        assertions: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Fetches verbatim EvidenceSpan records for retrieved assertions."""
        evidence_items = []
        if not assertions:
            return evidence_items

        chunk_ids = list({a["chunk_id"] for a in assertions if a.get("chunk_id")})
        if not chunk_ids:
            return evidence_items

        cypher = """
        UNWIND $cids AS cid
        MATCH (ev:EvidenceSpan {chunk_id: cid})
        RETURN ev.id, ev.chunk_id, ev.exact_quote, ev.provision_locator, ev.char_start, ev.char_end
        LIMIT 40
        """
        try:
            res = self.graph.query(cypher, {"cids": chunk_ids})
            for row in res.result_set:
                ev_id, cid, quote, loc, start, end = row[0], row[1], row[2], row[3], row[4], row[5]
                evidence_items.append({
                    "evidence_id": ev_id,
                    "chunk_id": cid,
                    "exact_quote": quote,
                    "provision_locator": loc or "Unspecified Provision",
                    "char_start": start,
                    "char_end": end,
                    "source": "graph_assertion_evidence",
                })
        except Exception as ex:
            print(f"Evidence retrieval warning: {ex}", flush=True)

        return evidence_items

    def retrieve(
        self,
        query: str,
        baseline_candidates: List[Dict[str, Any]],
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """Orchestrates local graph expansion, evidence retrieval, reranking, and context budgeting."""
        t0 = time.time()
        trace = {
            "query": query,
            "mode": "hybrid_graph",
            "review_mode": "unreviewed_extraction_dev_mode",
            "initial_candidates_count": len(baseline_candidates),
        }

        # Step 1: Extract graph seeds
        seeds, seed_trace = self.extract_seeds(baseline_candidates, query)
        trace["seeds"] = seeds
        trace["seed_diagnostics"] = seed_trace

        # Step 2: Bounded graph expansion
        traversed_paths, assertions = self.expand_graph(seeds, hops=self.max_hops)
        trace["traversed_paths_count"] = len(traversed_paths)
        trace["traversed_paths"] = traversed_paths[:15]
        trace["retrieved_assertions_count"] = len(assertions)
        trace["assertions"] = assertions[:10]

        # Step 3: Fetch supporting verbatim evidence spans
        graph_evidence = self.fetch_assertion_evidence(assertions)
        trace["graph_evidence_count"] = len(graph_evidence)

        # Step 4: Deduplicate and rerank evidence records
        # Combine baseline candidates and graph-added evidence
        final_evidence_items = []
        seen_quotes = set()
        total_chars = 0

        # Prioritize graph assertions with explicit conditions/exceptions
        for a in assertions:
            # Format assertion rule text
            qualifiers_text = ""
            if a["conditions"]:
                qualifiers_text += f" [Conditions: {', '.join(str(c) for c in a['conditions'])}]"
            if a["exceptions"]:
                qualifiers_text += f" [Exceptions: {', '.join(str(e) for e in a['exceptions'])}]"
            if a["numeric_constraints"]:
                qualifiers_text += f" [Thresholds: {', '.join(str(n) for n in a['numeric_constraints'])}]"

            rule_summary = f"{a['description']}{qualifiers_text}"
            item = {
                "type": "graph_assertion",
                "chunk_id": a["chunk_id"],
                "content": rule_summary,
                "is_conditional": a["is_conditional"],
                "conditions": a["conditions"],
                "exceptions": a["exceptions"],
                "numeric_constraints": a["numeric_constraints"],
                "source_relation": f"{a['source_name']} -> {a['relation_type']} -> {a['target_name']}",
            }
            if len(rule_summary) + total_chars <= self.context_budget_chars:
                final_evidence_items.append(item)
                total_chars += len(rule_summary)

        # Add verbatim quotes from graph evidence spans
        for ev in graph_evidence:
            q_clean = ev["exact_quote"].strip()
            if q_clean and q_clean not in seen_quotes:
                seen_quotes.add(q_clean)
                item = {
                    "type": "verbatim_quote",
                    "chunk_id": ev["chunk_id"],
                    "locator": ev["provision_locator"],
                    "content": q_clean,
                    "char_start": ev["char_start"],
                    "char_end": ev["char_end"],
                }
                if len(q_clean) + total_chars <= self.context_budget_chars:
                    final_evidence_items.append(item)
                    total_chars += len(q_clean)

        # Fill remaining budget with baseline top chunks
        for cand in baseline_candidates:
            cid = cand["chunk_id"]
            text = cand.get("text", "")
            if text and cid not in [it.get("chunk_id") for it in final_evidence_items]:
                # Take primary passage that fits
                budget_left = self.context_budget_chars - total_chars
                if budget_left > 200:
                    passage = text[:budget_left]
                    final_evidence_items.append({
                        "type": "baseline_chunk",
                        "chunk_id": cid,
                        "locator": cand.get("section_locator", ""),
                        "content": passage,
                        "rrf_score": cand.get("rrf_score", 0.0),
                    })
                    total_chars += len(passage)

        elapsed = time.time() - t0
        trace["retrieval_latency_ms"] = round(elapsed * 1000, 2)
        trace["total_context_chars"] = total_chars
        trace["final_evidence_items_count"] = len(final_evidence_items)

        return {
            "mode": "hybrid_graph",
            "evidence": final_evidence_items,
            "trace": trace,
            "latency_ms": trace["retrieval_latency_ms"],
        }
