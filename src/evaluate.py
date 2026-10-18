"""Automated Evaluation Harness for Dual-Mode Legal GraphRAG.
Compares baseline_hybrid against hybrid_graph across the 8 NBFC Pilot Questions.

Metrics recorded:
1. Retrieval Latency (ms) & Answer Generation Latency (ms)
2. Total Context Budget Utilization (chars out of 5,000 max)
3. Evidence Yield (baseline chunks vs graph assertions vs verbatim quotes)
4. Graph Expansion Statistics (seeds, traversed paths, assertions, quotes)
5. Conditions & Exceptions Coverage
6. Citations Grounding & Provenance Tracking
"""

import os
import sys
import json
import time
from typing import Dict, Any, List

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.abspath("."))

from src.rag_pipeline import LegalRAGPipeline
from src.answer_generator import LegalAnswerGenerator


def run_evaluation():
    questions_file = "data/pilot_questions.json"
    if not os.path.exists(questions_file):
        raise FileNotFoundError(f"Questions file {questions_file} not found.")

    with open(questions_file, "r", encoding="utf-8") as f:
        pilot_questions = json.load(f)

    print(f"\n================================================================================")
    print(f"STARTING SIDE-BY-SIDE RAG EVALUATION: 8 NBFC PILOT QUESTIONS")
    print(f"Corpus: RBI Master Directions | Context Budget: 5,000 Chars")
    print(f"Modes: [1] baseline_hybrid (FTS5 BM25 + Dense Bi-Encoder RRF)")
    print(f"       [2] hybrid_graph   (Baseline + 1-2 Hop FalkorDB Graph Expansion)")
    print(f"================================================================================\n")

    pipeline = LegalRAGPipeline(regulator="RBI", enable_graph=True, context_budget_chars=5000)
    pipeline.initialize()
    generator = LegalAnswerGenerator(dev_mode=True)

    eval_results = []

    for idx, item in enumerate(pilot_questions, 1):
        qid = item["id"]
        q_text = item["Compliance Question"]
        print(f"\n[{idx}/8] Evaluating Pilot Question ID {qid}:")
        print(f"     \"{q_text}\"")

        # ----------------------------------------------------
        # 1. Baseline Hybrid
        # ----------------------------------------------------
        t0 = time.time()
        base_retrieval = pipeline.retrieve(q_text, mode="baseline_hybrid", top_k=5)
        base_ret_ms = round((time.time() - t0) * 1000, 2)

        t0 = time.time()
        base_answer = generator.generate_answer(q_text, base_retrieval)
        base_gen_ms = round((time.time() - t0) * 1000, 2)

        # ----------------------------------------------------
        # 2. Hybrid Graph
        # ----------------------------------------------------
        t0 = time.time()
        graph_retrieval = pipeline.retrieve(q_text, mode="hybrid_graph", top_k=5)
        graph_ret_ms = round((time.time() - t0) * 1000, 2)

        t0 = time.time()
        graph_answer = generator.generate_answer(q_text, graph_retrieval)
        graph_gen_ms = round((time.time() - t0) * 1000, 2)

        # Extract Graph Metrics
        g_trace = graph_retrieval.get("trace", {})
        g_evidence = graph_retrieval.get("evidence", [])
        g_assertions = [e for e in g_evidence if e.get("type") == "graph_assertion"]
        g_quotes = [e for e in g_evidence if e.get("type") == "verbatim_quote"]
        g_conds = sum(len(a.get("conditions", [])) for a in g_assertions)
        g_excs = sum(len(a.get("exceptions", [])) for a in g_assertions)
        g_nums = sum(len(a.get("numeric_constraints", [])) for a in g_assertions)

        print(f"  [Baseline] Latency: {base_ret_ms + base_gen_ms:.1f}ms (Ret: {base_ret_ms}ms, Gen: {base_gen_ms}ms) | Evidence: {len(base_retrieval['evidence'])} chunks | Context: {base_retrieval.get('total_context_chars', 0)} chars")
        print(f"  [Graph]    Latency: {graph_ret_ms + graph_gen_ms:.1f}ms (Ret: {graph_ret_ms}ms, Gen: {graph_gen_ms}ms) | Evidence: {len(g_evidence)} items ({len(g_assertions)} assertions, {len(g_quotes)} quotes) | Context: {g_trace.get('total_context_chars', 0)} chars")
        print(f"             Seeds: {len(g_trace.get('seeds', []))} | Paths: {g_trace.get('traversed_paths_count', 0)} | Qualifiers: {g_conds} conds, {g_excs} excs, {g_nums} nums")

        eval_results.append({
            "question_id": qid,
            "question": q_text,
            "metrics": {
                "baseline_retrieval_ms": base_ret_ms,
                "baseline_generation_ms": base_gen_ms,
                "baseline_total_ms": base_ret_ms + base_gen_ms,
                "baseline_evidence_count": len(base_retrieval["evidence"]),
                "baseline_context_chars": base_retrieval.get("total_context_chars", 0),
                "baseline_citations_count": len(base_answer["citations"]),
                "graph_retrieval_ms": graph_ret_ms,
                "graph_generation_ms": graph_gen_ms,
                "graph_total_ms": graph_ret_ms + graph_gen_ms,
                "graph_evidence_count": len(g_evidence),
                "graph_assertions_count": len(g_assertions),
                "graph_quotes_count": len(g_quotes),
                "graph_context_chars": g_trace.get("total_context_chars", 0),
                "graph_seeds_count": len(g_trace.get("seeds", [])),
                "graph_traversed_paths_count": g_trace.get("traversed_paths_count", 0),
                "graph_conditions_uncovered": g_conds,
                "graph_exceptions_uncovered": g_excs,
                "graph_numeric_constraints_uncovered": g_nums,
                "graph_citations_count": len(graph_answer["citations"]),
            },
            "baseline_result": {
                "answer": base_answer["answer"],
                "analysis": base_answer["legal_analysis"],
                "rule_determination": base_answer["applicable_layer_or_rule"],
                "citations": base_answer["citations"],
                "evidence": base_retrieval["evidence"][:3],
            },
            "graph_result": {
                "answer": graph_answer["answer"],
                "analysis": graph_answer["legal_analysis"],
                "rule_determination": graph_answer["applicable_layer_or_rule"],
                "conditions_exceptions": graph_answer["conditions_and_exceptions"],
                "citations": graph_answer["citations"],
                "evidence": g_evidence[:5],
                "traversed_paths": g_trace.get("traversed_paths", [])[:5],
            },
            "provenance": {
                "review_status": "unreviewed",
                "execution_mode": "unreviewed_extraction_dev_mode",
            }
        })

    # Summary Statistics
    avg_base_ret = sum(r["metrics"]["baseline_retrieval_ms"] for r in eval_results) / len(eval_results)
    avg_graph_ret = sum(r["metrics"]["graph_retrieval_ms"] for r in eval_results) / len(eval_results)
    avg_base_items = sum(r["metrics"]["baseline_evidence_count"] for r in eval_results) / len(eval_results)
    avg_graph_items = sum(r["metrics"]["graph_evidence_count"] for r in eval_results) / len(eval_results)
    total_conds = sum(r["metrics"]["graph_conditions_uncovered"] for r in eval_results)
    total_excs = sum(r["metrics"]["graph_exceptions_uncovered"] for r in eval_results)

    print("\n" + "=" * 80)
    print("EVALUATION SUMMARY:")
    print(f"  Avg Baseline Retrieval Latency: {avg_base_ret:.1f} ms")
    print(f"  Avg Graph Retrieval Latency:    {avg_graph_ret:.1f} ms")
    print(f"  Avg Baseline Evidence Count:    {avg_base_items:.1f} items")
    print(f"  Avg Graph Evidence Count:       {avg_graph_items:.1f} items (Assertions + Verbatim Spans)")
    print(f"  Total Conditions Uncovered:     {total_conds}")
    print(f"  Total Exceptions Uncovered:     {total_excs}")
    print("=" * 80 + "\n")

    output_path = "data/evaluation_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(eval_results, f, indent=2, ensure_ascii=False)
    print(f"Saved evaluation results to {output_path}")

    return eval_results


if __name__ == "__main__":
    run_evaluation()
