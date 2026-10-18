import os
import sys
import json
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.abspath("."))

from src.rag_pipeline import LegalRAGPipeline

def run_pilot():
    questions_file = "data/pilot_questions.json"
    with open(questions_file, "r", encoding="utf-8") as f:
        questions = json.load(f)

    pipeline = LegalRAGPipeline(regulator="RBI", enable_graph=True, context_budget_chars=5000)
    pipeline.initialize()

    results = []

    print(f"\nEvaluating {len(questions)} Pilot NBFC Questions across dual retrieval modes...")
    print("=" * 80)

    for q in questions:
        qid = q["id"]
        q_text = q["Compliance Question"]
        print(f"\n[Q{qid}] {q_text}")

        # 1. Baseline Hybrid
        t0 = time.time()
        base_res = pipeline.retrieve(q_text, mode="baseline_hybrid", top_k=5)
        base_lat = round((time.time() - t0) * 1000, 2)

        # 2. Hybrid Graph
        t0 = time.time()
        graph_res = pipeline.retrieve(q_text, mode="hybrid_graph", top_k=5)
        graph_lat = round((time.time() - t0) * 1000, 2)

        base_evidence = base_res.get("evidence", [])
        graph_evidence = graph_res.get("evidence", [])
        graph_trace = graph_res.get("trace", {})

        print(f"  -> Baseline: {len(base_evidence)} items, {base_res.get('total_chars', 0)} chars, {base_lat} ms")
        print(f"  -> Graph:    {len(graph_evidence)} items, {graph_trace.get('total_context_chars', 0)} chars, {graph_lat} ms")
        print(f"     Seeds: {len(graph_trace.get('seeds', []))}, Paths: {graph_trace.get('traversed_paths_count', 0)}, Assertions: {graph_trace.get('retrieved_assertions_count', 0)}, Quotes: {graph_trace.get('graph_evidence_count', 0)}")

        # Count conditions and exceptions in graph evidence
        cond_count = sum(len(it.get("conditions", [])) for it in graph_evidence if it.get("type") == "graph_assertion")
        exc_count = sum(len(it.get("exceptions", [])) for it in graph_evidence if it.get("type") == "graph_assertion")

        results.append({
            "question_id": qid,
            "question": q_text,
            "baseline": {
                "latency_ms": base_lat,
                "evidence_count": len(base_evidence),
                "total_chars": base_res.get("total_chars", 0),
                "evidence": base_evidence,
            },
            "hybrid_graph": {
                "latency_ms": graph_lat,
                "evidence_count": len(graph_evidence),
                "total_chars": graph_trace.get("total_context_chars", 0),
                "seeds_count": len(graph_trace.get("seeds", [])),
                "paths_count": graph_trace.get("traversed_paths_count", 0),
                "assertions_count": graph_trace.get("retrieved_assertions_count", 0),
                "quotes_count": graph_trace.get("graph_evidence_count", 0),
                "conditions_retrieved": cond_count,
                "exceptions_retrieved": exc_count,
                "evidence": graph_evidence,
                "trace": graph_trace,
            }
        })

    out_file = "data/pilot_retrieval_comparison.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 80)
    print(f"Completed retrieval run for all 8 pilot questions.")
    print(f"Saved full comparison to {out_file}")

if __name__ == "__main__":
    run_pilot()
