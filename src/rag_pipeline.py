"""Unified Legal GraphRAG Pipeline for Indian Regulatory Corpus.
Supports four independently runnable retrieval modes plus intelligent auto-routing:
1. 'baseline_hybrid': Lexical (BM25) + Dense Semantic (Bi-encoder) with RRF fusion
2. 'hybrid_graph': Baseline retrieval + Local Graph Expansion (FalkorDB 1-2 hops) + Evidence Reranking
3. 'global_community': Hierarchical Leiden Community Report Map-Reduce (global themes)
4. 'global_with_evidence': Global Community Map-Reduce resolved to original verbatim text passages

Includes automatic fallback on database unavailability and complete diagnostic traces.
"""

import os
import sys
import time
import re
from typing import Dict, Any, List, Optional, Tuple

sys.path.insert(0, os.path.abspath("."))
from src.baseline_retriever import BaselineHybridRetriever
from src.graph_retriever import LocalGraphRetriever
from src.global_search import GlobalCommunitySearcher
from src.config import FALKORDB_GRAPH_NAME

ALLOWED_MODES = [
    "baseline_hybrid",
    "hybrid_graph",
    "global_community",
    "global_with_evidence",
    "auto",
]


class LegalRAGPipeline:
    """Unified Legal GraphRAG pipeline exposing all retrieval modes and auto-routing."""

    def __init__(
        self,
        regulator: str = "RBI",
        graph_name: str = FALKORDB_GRAPH_NAME,
        context_budget_chars: int = 5000,
        enable_graph: bool = True,
    ):
        self.regulator = regulator
        self.graph_name = graph_name
        self.context_budget_chars = context_budget_chars
        self.enable_graph = enable_graph

        # Initialize Retrievers
        self.baseline_retriever = BaselineHybridRetriever(regulator=regulator)
        self.graph_retriever: Optional[LocalGraphRetriever] = None
        self.global_searcher: Optional[GlobalCommunitySearcher] = None

        if enable_graph:
            try:
                self.graph_retriever = LocalGraphRetriever(
                    graph_name=graph_name,
                    context_budget_chars=context_budget_chars,
                )
                self.global_searcher = GlobalCommunitySearcher(
                    graph_name=graph_name,
                    reduction_budget_chars=context_budget_chars,
                )
            except Exception as ex:
                print(f"Warning: Graph initialization failed: {ex}. Pipeline will operate with baseline fallback.", flush=True)

    def initialize(self):
        """Pre-loads indexes and verifies component readiness."""
        print("Initializing LegalRAGPipeline components...", flush=True)
        self.baseline_retriever.build_or_load_index()
        print("LegalRAGPipeline ready.", flush=True)

    def determine_route(self, query: str) -> Tuple[str, str]:
        """Classifies query into local hybrid_graph vs global_with_evidence."""
        q_lower = query.lower()

        # Specific indicators: section/paragraph numbers, exact crore numbers, specific roles/deadlines
        specific_patterns = [
            r"section \d+", r"paragraph \d+", r"para \d+",
            r"₹?\d+\s*crore", r"tenure", r"dual-hatting",
            r"type-i", r"mid-year", r"standalone"
        ]
        for pat in specific_patterns:
            if re.search(pat, q_lower):
                return "hybrid_graph", f"Matched specific regulatory pattern '{pat}'"

        # Thematic indicators: broad corpus questions
        thematic_keywords = ["recurring", "patterns", "overview", "governance across", "all categories", "framework"]
        for kw in thematic_keywords:
            if kw in q_lower:
                return "global_with_evidence", f"Matched global thematic keyword '{kw}'"

        # Default fallback for compliance questions
        return "hybrid_graph", "Default compliance question route"

    def retrieve(
        self,
        query: str,
        mode: str = "hybrid_graph",
        top_k: int = 5,
        target_level: int = 0,
    ) -> Dict[str, Any]:
        """Executes retrieval under the specified mode or auto-routed mode."""
        t0 = time.time()

        if mode not in ALLOWED_MODES:
            raise ValueError(f"Invalid retrieval mode: '{mode}'. Allowed modes: {ALLOWED_MODES}")

        # Auto-routing
        routing_diagnostic = None
        if mode == "auto":
            routed_mode, reason = self.determine_route(query)
            routing_diagnostic = {
                "requested_mode": "auto",
                "selected_mode": routed_mode,
                "routing_reason": reason,
            }
            mode = routed_mode

        # -----------------------------------------------------------------
        # Mode 3 & 4: Global Search Modes (Map-Reduce across Communities)
        # -----------------------------------------------------------------
        if mode in ["global_community", "global_with_evidence"]:
            if not self.enable_graph or self.global_searcher is None:
                print(f"Warning: Global searcher unavailable. Falling back to baseline_hybrid.", flush=True)
                return self._fallback_baseline(query, t0, diagnostic="Global searcher unavailable; fell back to baseline.")

            res = self.global_searcher.search(
                query=query,
                target_level=target_level,
                with_original_evidence=(mode == "global_with_evidence"),
            )
            if routing_diagnostic:
                res["trace"]["routing_diagnostic"] = routing_diagnostic
            return res

        # -----------------------------------------------------------------
        # Mode 1 & 2: Local Search Modes (Baseline & Hybrid Graph)
        # -----------------------------------------------------------------
        baseline_candidates = self.baseline_retriever.search(query, top_k=top_k)

        # Baseline mode or graph disabled fallback
        is_fallback = bool(mode == "hybrid_graph" and (not self.enable_graph or self.graph_retriever is None))
        if mode == "baseline_hybrid" or is_fallback:
            total_chars = 0
            evidence_items = []
            for cand in baseline_candidates:
                txt = cand.get("text", "")
                budget_left = self.context_budget_chars - total_chars
                if budget_left <= 0:
                    break
                snippet = txt[:budget_left]
                evidence_items.append({
                    "type": "baseline_chunk_fallback" if is_fallback else "baseline_chunk",
                    "chunk_id": cand["chunk_id"],
                    "document_id": cand.get("document_id", ""),
                    "locator": cand.get("section_locator", "Unspecified Section"),
                    "content": snippet,
                    "rrf_score": cand["rrf_score"],
                })
                total_chars += len(snippet)

            elapsed = time.time() - t0
            ret_dict = {
                "mode": "baseline_hybrid_fallback" if is_fallback else "baseline_hybrid",
                "evidence": evidence_items,
                "latency_ms": round(elapsed * 1000, 2),
                "total_context_chars": total_chars,
                "trace": {
                    "mode": "baseline_hybrid_fallback" if is_fallback else "baseline_hybrid",
                    "review_mode": "unreviewed_extraction_dev_mode",
                    "candidates_retrieved": len(baseline_candidates),
                    "retrieval_latency_ms": round(elapsed * 1000, 2),
                }
            }
            if is_fallback:
                ret_dict["diagnostic"] = "Graph retrieval disabled or unavailable; safely fell back to baseline_hybrid."
                ret_dict["trace"]["diagnostic"] = ret_dict["diagnostic"]
            if routing_diagnostic:
                ret_dict["trace"]["routing_diagnostic"] = routing_diagnostic
            return ret_dict

        # Hybrid Graph Retrieval
        try:
            graph_result = self.graph_retriever.retrieve(
                query=query,
                baseline_candidates=baseline_candidates,
                top_k=top_k,
            )
            elapsed = time.time() - t0
            graph_result["total_latency_ms"] = round(elapsed * 1000, 2)
            if routing_diagnostic:
                graph_result["trace"]["routing_diagnostic"] = routing_diagnostic
            return graph_result
        except Exception as ex:
            return self._fallback_baseline(query, t0, str(ex))

    def _fallback_baseline(self, query: str, start_time: float, diagnostic: str) -> Dict[str, Any]:
        """Provides safe fallback to baseline candidates on any graph error."""
        baseline_candidates = self.baseline_retriever.search(query, top_k=5)
        total_chars = 0
        evidence_items = []
        for cand in baseline_candidates:
            txt = cand.get("text", "")
            budget_left = self.context_budget_chars - total_chars
            if budget_left <= 0:
                break
            snippet = txt[:budget_left]
            evidence_items.append({
                "type": "baseline_chunk_fallback",
                "chunk_id": cand["chunk_id"],
                "locator": cand.get("section_locator", ""),
                "content": snippet,
                "rrf_score": cand["rrf_score"],
            })
            total_chars += len(snippet)

        elapsed = time.time() - start_time
        return {
            "mode": "baseline_hybrid_fallback",
            "evidence": evidence_items,
            "latency_ms": round(elapsed * 1000, 2),
            "total_context_chars": total_chars,
            "diagnostic": diagnostic,
            "trace": {
                "mode": "fallback_due_to_error",
                "diagnostic": diagnostic,
                "review_mode": "unreviewed_extraction_dev_mode",
            }
        }
