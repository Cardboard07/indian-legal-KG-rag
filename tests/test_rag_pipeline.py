import sys
import os
import json
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.abspath("."))

from src.rag_pipeline import LegalRAGPipeline

pipeline = LegalRAGPipeline(enable_graph=True)
pipeline.initialize()

test_query = "If our standalone asset size is ₹600 crore, but our group NBFCs aggregate to ₹1,200 crore, are we treated as Base Layer (NBFC-BL) or Middle Layer (NBFC-ML)?"

print(f"\n==========================================")
print(f"TESTING RETRIEVAL MODES ON PILOT QUESTION 3:")
print(f"'{test_query}'")
print(f"==========================================\n")

# 1. Baseline Hybrid Retrieval
t0 = time.time()
res_baseline = pipeline.retrieve(test_query, mode="baseline_hybrid", top_k=5)
t_base = time.time() - t0

print(f"--- 1. BASELINE HYBRID RETRIEVAL ({t_base*1000:.1f}ms) ---")
print(f"Mode: {res_baseline['mode']}")
print(f"Total Context Chars: {res_baseline['total_context_chars']}")
print(f"Evidence items count: {len(res_baseline['evidence'])}")
for i, ev in enumerate(res_baseline["evidence"][:3], 1):
    print(f"  [{i}] {ev['type']} | {ev['chunk_id']} | Locator: {ev.get('locator', '')}")
    print(f"      Text: {ev['content'][:140]}...\n")

# 2. Hybrid Graph Retrieval (with FalkorDB expansion)
t0 = time.time()
res_graph = pipeline.retrieve(test_query, mode="hybrid_graph", top_k=5)
t_graph = time.time() - t0

print(f"--- 2. HYBRID GRAPH RETRIEVAL ({t_graph*1000:.1f}ms) ---")
print(f"Mode: {res_graph['mode']}")
print(f"Seeds found: {len(res_graph['trace'].get('seeds', []))}")
for s in res_graph['trace'].get('seeds', [])[:4]:
    print(f"  Seed: {s['name']} ({s['type']}) [source: {s['source']}]")

print(f"Traversed paths count: {res_graph['trace'].get('traversed_paths_count', 0)}")
for p in res_graph['trace'].get('traversed_paths', [])[:4]:
    print(f"  Path: ({p['source_name']}) -[{p['relation_type']}]-> ({p['target_name']}) [conditional: {p['is_conditional']}]")

print(f"\nRetrieved assertions count: {res_graph['trace'].get('retrieved_assertions_count', 0)}")
print(f"Graph evidence count: {res_graph['trace'].get('graph_evidence_count', 0)}")
print(f"Final evidence items in context budget: {len(res_graph['evidence'])}")
for i, ev in enumerate(res_graph["evidence"][:4], 1):
    print(f"  [{i}] Type: {ev['type']} | Chunk: {ev.get('chunk_id')}")
    print(f"      Content: {ev['content'][:140]}...\n")
