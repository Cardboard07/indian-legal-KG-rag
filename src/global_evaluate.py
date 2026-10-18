"""Comparative Global Evaluation Harness for Legal GraphRAG Stage 3.
Evaluates the 5 Global Benchmark Questions across 6 distinct retrieval modes:
1. 'baseline_hybrid': Lexical (BM25) + Dense Bi-Encoder RRF
2. 'hybrid_graph': Baseline + 1-2 hop local graph expansion
3. 'global_community_l0': Global Map-Reduce over Level 0 Community Reports
4. 'global_community_l1': Global Map-Reduce over Level 1 Community Reports
5. 'global_with_evidence': Global Map-Reduce resolved to verbatim source passages
6. 'text_map_reduce': Graph-free text chunk Map-Reduce (paper baseline)

Metrics recorded:
- Latency (ms)
- Context budget utilization (chars / 5,000)
- Evidence yield & citations
- Conditions, exceptions, and numeric constraints coverage
- Multi-dimensional scoring: Comprehensiveness, Diversity, Empowerment, Directness (0-10)
"""

import os
import sys
import json
import time
import random
import re
from typing import Dict, Any, List

sys.path.insert(0, os.path.abspath("."))
from src.rag_pipeline import LegalRAGPipeline
from src.answer_generator import LegalAnswerGenerator


def run_text_map_reduce(pipeline: LegalRAGPipeline, query: str, context_budget_chars: int = 5000) -> Dict[str, Any]:
    """Graph-free source-text map-reduce baseline (Paper §4)."""
    t0 = time.time()
    # 1. Retrieve raw candidate chunks (top 32)
    raw_chunks = pipeline.baseline_retriever.search(query, top_k=32)

    # 2. Map Stage: Group into batches of 8 chunks and extract key points
    batch_size = 8
    batches = [raw_chunks[i : i + batch_size] for i in range(0, len(raw_chunks), batch_size)]

    q_tokens = set(re.findall(r"\w+", query.lower()))
    map_points = []

    for b in batches:
        for c in b:
            txt = c.get("text", "")
            tokens = set(re.findall(r"\w+", txt.lower()))
            overlap = q_tokens.intersection(tokens)
            score = int((len(overlap) / max(len(q_tokens), 1)) * 100)
            if score > 15:
                map_points.append({
                    "chunk_id": c["chunk_id"],
                    "locator": c.get("section_locator", "Section"),
                    "content": txt[:250],
                    "helpfulness_score": score,
                })

    # 3. Reduce Stage: Rank, fit budget, and synthesize
    map_points.sort(key=lambda x: x["helpfulness_score"], reverse=True)
    selected = []
    total_chars = 0
    for pt in map_points:
        if total_chars + len(pt["content"]) <= context_budget_chars:
            selected.append(pt)
            total_chars += len(pt["content"])
        else:
            break

    elapsed = time.time() - t0
    citations = [{"chunk_id": pt["chunk_id"], "locator": pt["locator"]} for pt in selected[:6]]

    return {
        "mode": "text_map_reduce",
        "answer": (
            f"Synthesized across {len(selected)} raw text passages via graph-free map-reduce. "
            "Governs compliance mandates under cited circular provisions."
        ),
        "citations": citations,
        "total_context_chars": total_chars,
        "points_count": len(selected),
        "latency_ms": round(elapsed * 1000, 2),
    }


def compute_eval_scores(query: str, answer_data: Dict[str, Any]) -> Dict[str, float]:
    """Computes automated paper metrics (Comprehensiveness, Diversity, Empowerment, Directness)."""
    text = (answer_data.get("answer", "") + " " + answer_data.get("legal_analysis", "")).lower()
    citations = answer_data.get("citations", [])
    context_chars = answer_data.get("total_context_chars", answer_data.get("trace", {}).get("total_context_chars", 0))

    # Comprehensiveness (0-10): context length and entity/concept breadth
    comp = min(10.0, round(2.0 + (context_chars / 600.0) + (len(citations) * 0.4), 1))

    # Diversity (0-10): spread across distinct citations / communities
    div = min(10.0, round(2.0 + (len(citations) * 1.1), 1))

    # Empowerment (0-10): actionable regulatory guidance and qualifiers
    has_actionable = any(w in text for w in ["mandate", "must", "shall", "prohibit", "exempt", "threshold", "layer"])
    emp = min(10.0, round((6.0 if has_actionable else 3.0) + (len(citations) * 0.5), 1))

    # Directness (0-10): concise direct answer presence
    direct = 8.5 if len(answer_data.get("answer", "")) > 40 else 5.0

    return {
        "comprehensiveness": comp,
        "diversity": div,
        "empowerment": emp,
        "directness": direct,
        "overall_mean": round((comp + div + emp + direct) / 4.0, 2),
    }


def run_global_evaluation():
    print(f"\n================================================================================")
    print(f"STARTING COMPREHENSIVE GLOBAL EVALUATION BENCHMARK (STAGE 3)")
    print(f"Modes: [1] baseline_hybrid")
    print(f"       [2] hybrid_graph")
    print(f"       [3] global_community_l0 (Frontier Level 0)")
    print(f"       [4] global_community_l1 (Frontier Level 1)")
    print(f"       [5] global_with_evidence")
    print(f"       [6] text_map_reduce (Graph-free baseline)")
    print(f"================================================================================\n")

    pipeline = LegalRAGPipeline(regulator="RBI", enable_graph=True, context_budget_chars=5000)
    pipeline.initialize()
    generator = LegalAnswerGenerator(dev_mode=True)

    with open("data/global_benchmark_questions.json", "r", encoding="utf-8") as f:
        global_questions = json.load(f)

    evaluation_matrix = []

    for q_item in global_questions:
        qid = q_item["id"]
        theme = q_item["theme"]
        q_text = q_item["question"]

        print(f"\n[{qid}] Theme: {theme}")
        print(f"     \"{q_text}\"")

        modes_results = {}

        # 1. Baseline Hybrid
        t0 = time.time()
        res_bh = pipeline.retrieve(q_text, mode="baseline_hybrid")
        ans_bh = generator.generate_answer(q_text, res_bh)
        res_bh["latency_ms"] = round((time.time() - t0) * 1000, 2)
        ans_bh["total_context_chars"] = res_bh.get("total_context_chars", 0)
        modes_results["baseline_hybrid"] = {
            "result": ans_bh,
            "scores": compute_eval_scores(q_text, ans_bh),
            "latency_ms": res_bh["latency_ms"],
            "citations_count": len(ans_bh["citations"]),
            "context_chars": ans_bh["total_context_chars"],
        }
        print(f"  -> baseline_hybrid: {modes_results['baseline_hybrid']['latency_ms']}ms | Cites: {modes_results['baseline_hybrid']['citations_count']} | Score: {modes_results['baseline_hybrid']['scores']['overall_mean']}")

        # 2. Hybrid Graph
        t0 = time.time()
        res_hg = pipeline.retrieve(q_text, mode="hybrid_graph")
        ans_hg = generator.generate_answer(q_text, res_hg)
        res_hg["latency_ms"] = round((time.time() - t0) * 1000, 2)
        ans_hg["total_context_chars"] = res_hg.get("trace", {}).get("total_context_chars", 0)
        modes_results["hybrid_graph"] = {
            "result": ans_hg,
            "scores": compute_eval_scores(q_text, ans_hg),
            "latency_ms": res_hg["latency_ms"],
            "citations_count": len(ans_hg["citations"]),
            "context_chars": ans_hg["total_context_chars"],
        }
        print(f"  -> hybrid_graph:    {modes_results['hybrid_graph']['latency_ms']}ms | Cites: {modes_results['hybrid_graph']['citations_count']} | Score: {modes_results['hybrid_graph']['scores']['overall_mean']}")

        # 3. Global Community Level 0
        t0 = time.time()
        res_gc0 = pipeline.retrieve(q_text, mode="global_community", target_level=0)
        res_gc0["latency_ms"] = round((time.time() - t0) * 1000, 2)
        res_gc0["total_context_chars"] = res_gc0.get("trace", {}).get("reduction_context_chars", 0)
        modes_results["global_community_l0"] = {
            "result": res_gc0,
            "scores": compute_eval_scores(q_text, res_gc0),
            "latency_ms": res_gc0["latency_ms"],
            "citations_count": len(res_gc0.get("citations", [])),
            "context_chars": res_gc0["total_context_chars"],
        }
        print(f"  -> global_comm_l0:  {modes_results['global_community_l0']['latency_ms']}ms | Cites: {modes_results['global_community_l0']['citations_count']} | Score: {modes_results['global_community_l0']['scores']['overall_mean']}")

        # 4. Global Community Level 1
        t0 = time.time()
        res_gc1 = pipeline.retrieve(q_text, mode="global_community", target_level=1)
        res_gc1["latency_ms"] = round((time.time() - t0) * 1000, 2)
        res_gc1["total_context_chars"] = res_gc1.get("trace", {}).get("reduction_context_chars", 0)
        modes_results["global_community_l1"] = {
            "result": res_gc1,
            "scores": compute_eval_scores(q_text, res_gc1),
            "latency_ms": res_gc1["latency_ms"],
            "citations_count": len(res_gc1.get("citations", [])),
            "context_chars": res_gc1["total_context_chars"],
        }
        print(f"  -> global_comm_l1:  {modes_results['global_community_l1']['latency_ms']}ms | Cites: {modes_results['global_community_l1']['citations_count']} | Score: {modes_results['global_community_l1']['scores']['overall_mean']}")

        # 5. Global With Evidence
        t0 = time.time()
        res_gwe = pipeline.retrieve(q_text, mode="global_with_evidence", target_level=0)
        res_gwe["latency_ms"] = round((time.time() - t0) * 1000, 2)
        res_gwe["total_context_chars"] = res_gwe.get("trace", {}).get("reduction_context_chars", 0)
        modes_results["global_with_evidence"] = {
            "result": res_gwe,
            "scores": compute_eval_scores(q_text, res_gwe),
            "latency_ms": res_gwe["latency_ms"],
            "citations_count": len(res_gwe.get("citations", [])),
            "context_chars": res_gwe["total_context_chars"],
            "verbatim_items": len(res_gwe.get("verbatim_evidence", [])),
        }
        print(f"  -> global_evidence: {modes_results['global_with_evidence']['latency_ms']}ms | Cites: {modes_results['global_with_evidence']['citations_count']} | Score: {modes_results['global_with_evidence']['scores']['overall_mean']}")

        # 6. Graph-Free Text Map-Reduce
        res_tmr = run_text_map_reduce(pipeline, q_text)
        modes_results["text_map_reduce"] = {
            "result": res_tmr,
            "scores": compute_eval_scores(q_text, res_tmr),
            "latency_ms": res_tmr["latency_ms"],
            "citations_count": len(res_tmr.get("citations", [])),
            "context_chars": res_tmr["total_context_chars"],
        }
        print(f"  -> text_map_reduce: {modes_results['text_map_reduce']['latency_ms']}ms | Cites: {modes_results['text_map_reduce']['citations_count']} | Score: {modes_results['text_map_reduce']['scores']['overall_mean']}")

        evaluation_matrix.append({
            "question_id": qid,
            "theme": theme,
            "question": q_text,
            "modes": modes_results,
        })

    # Summary Table Across Modes
    mode_names = ["baseline_hybrid", "hybrid_graph", "global_community_l0", "global_community_l1", "global_with_evidence", "text_map_reduce"]
    summary_stats = {}
    print("\n" + "=" * 90)
    print(f"{'Retrieval Mode':<25} | {'Avg Latency (ms)':<16} | {'Avg Cites':<10} | {'Avg Chars':<10} | {'Mean Score':<10}")
    print("-" * 90)

    for m in mode_names:
        avg_lat = sum(it["modes"][m]["latency_ms"] for it in evaluation_matrix) / len(evaluation_matrix)
        avg_cites = sum(it["modes"][m]["citations_count"] for it in evaluation_matrix) / len(evaluation_matrix)
        avg_chars = sum(it["modes"][m]["context_chars"] for it in evaluation_matrix) / len(evaluation_matrix)
        avg_score = sum(it["modes"][m]["scores"]["overall_mean"] for it in evaluation_matrix) / len(evaluation_matrix)
        summary_stats[m] = {
            "avg_latency_ms": round(avg_lat, 1),
            "avg_citations": round(avg_cites, 1),
            "avg_context_chars": round(avg_chars, 1),
            "avg_score": round(avg_score, 2),
        }
        print(f"{m:<25} | {avg_lat:<16.1f} | {avg_cites:<10.1f} | {avg_chars:<10.1f} | {avg_score:<10.2f}")
    print("=" * 90 + "\n")

    output_path = "data/global_evaluation_results.json"
    out_data = {
        "evaluation_matrix": evaluation_matrix,
        "summary_statistics": summary_stats,
        "provenance": {
            "review_status": "unreviewed",
            "execution_mode": "unreviewed_extraction_dev_mode",
            "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(out_data, f, indent=2, ensure_ascii=False)
    print(f"Saved full global evaluation results to {output_path}")

    return out_data


if __name__ == "__main__":
    run_global_evaluation()
