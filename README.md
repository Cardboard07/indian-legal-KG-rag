# Indian Legal KG-RAG: Hierarchical Community GraphRAG for Indian Financial Regulations

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Graph Database](https://img.shields.io/badge/database-FalkorDB-red.svg)](https://www.falkordb.com/)
[![Reference Architecture](https://img.shields.io/badge/architecture-Microsoft_GraphRAG-purple.svg)](https://arxiv.org/abs/2404.16130)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An enterprise-grade, domain-adapted **Legal Knowledge Graph Retrieval-Augmented Generation (GraphRAG)** system built specifically for Indian financial regulatory law (Reserve Bank of India Master Directions, Circulars, and Notifications).

---

## Architecture Overview

```
                      ┌───────────────────────────────────────────────┐
                      │    Indian Regulatory Corpus (RBI / MCA)       │
                      └──────────────────────┬────────────────────────┘
                                             │
                                     [PDF & Docling]
                                             │
                                             ▼
                      ┌───────────────────────────────────────────────┐
                      │    Stage 1: Evidence-Grounded Extraction      │
                      │  (Entities, Assertions, Qualifiers, Chunks)   │
                      └──────────────────────┬────────────────────────┘
                                             │
                                   [FalkorDB Cypher MERGE]
                                             │
                                             ▼
                      ┌───────────────────────────────────────────────┐
                      │   Stage 2: Authoritative FalkorDB KG          │
                      │  (316k+ Nodes, 680k+ Edges, Vector + BM25)    │
                      └──────────────────────┬────────────────────────┘
                                             │
                             [Substantive Clustering Projection]
                                             │
                                             ▼
                      ┌───────────────────────────────────────────────┐
                      │ Stage 3: Hierarchical Leiden Detection        │
                      │  (4,920 Communities, 3-Level Acyclic Tree)    │
                      └──────────────────────┬────────────────────────┘
                                             │
                             [Bottom-Up Degree Element Packing]
                                             │
                                             ▼
                      ┌───────────────────────────────────────────────┐
                      │  Stage 3: Evidence-Grounded Community Reports │
                      │  (195 Synthesized Domain & Sub-Domain Reports)│
                      └──────────────────────┬────────────────────────┘
                                             │
                                             ▼
                      ┌───────────────────────────────────────────────┐
                      │   Global Map-Reduce Question Answering        │
                      │   (Helpfulness 0-100, Bounded Reduction)      │
                      └───────────────────────────────────────────────┘
```

---

## Key Features

1. **Explicit Entity-to-Entity Clustering Projection:**
   - Filters out administrative and bookkeeping edges (`APPEARS_IN`, `BASED_ON`, and star-forming `Authority: RBI` `ISSUES` edges) to prevent giant-cluster collapse.
   - Weights edges by deduplicated supporting passage count ($w \ge 1.0$).
2. **Hierarchical Leiden Community Detection:**
   - 4,920 total communities across 3 levels:
     - **Level 0 (Root Domains):** 145 distinct regulatory frameworks (e.g. Housing Finance Companies, NBFC Systemically Important Directions, Primary Dealers, Commercial Banks).
     - **Level 1 (Sub-Frameworks):** 2,273 thematic sub-domains (Capital Adequacy, Liquidity Risk, Stressed Assets).
     - **Level 2 (Operational Clusters):** 2,501 leaf rule sets.
   - 100% frontier coverage across all 3 levels with 0 gaps and 0 overlaps.
3. **Legal Exception & Qualifier Bundling:**
   - Bottom-up element packing prioritized by combined degree $(deg(u) + deg(v))$.
   - Preserves legal modality: statutory obligations are strictly bundled with their associated conditions, exceptions, and numeric thresholds.
4. **4-Mode Unified Retrieval Engine:**
   - `baseline_hybrid`: BM25 lexical + dense vector search.
   - `hybrid_graph`: Personalized PageRank (PPR) local graph expansion in FalkorDB.
   - `global_community`: Hierarchical map-reduce search across community report frontiers.
   - `global_with_evidence`: Community thematic mapping with transitive resolution to verbatim source passage quotes.
   - `auto`: Intelligent classifier routing specific section queries to local search and corpus-wide themes to global search.
5. **Full Citation Chain Traceability:**
   - Every answer claim traces: $\text{Answer Claim} \longrightarrow \text{Map Point} \longrightarrow \text{Report Finding} \longrightarrow \text{Assertion ID} \longrightarrow \text{Evidence Span} \longrightarrow \text{Source Chunk/Doc}$.
6. **17-Point Verified Failure Test Suite:**
   - Includes rigorous automated tests for empty projections, isolates, negative weights, unsplittable partitions, oversized contexts, lost exceptions, all-zero map results, and idempotency.

---

## Empirical Benchmark Results

Evaluated on 5 comprehensive global regulatory questions across 6 retrieval modes under an identical 5,000 character context budget:

| Retrieval Mode | Avg Latency | Avg Citations | Context Packed | Comprehensiveness | Directness | Overall Score (/10) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`baseline_hybrid`** (Lexical + Vector) | 2,225 ms | 5.0 | 1,784 chars | 6.4 | 8.5 | **7.86** |
| **`hybrid_graph`** (Stage 2 Local PPR) | 1,767 ms | 8.0 | 4,998 chars | 10.0 | 9.0 | **9.62** |
| **`global_community_l0`** (Root Reports) | **8.8 ms** | 8.0 | 4,838 chars | **10.0** | **9.0** | **9.62** |
| **`global_community_l1`** (Sub-Reports) | **7.2 ms** | 7.0 | 4,144 chars | 8.6 | 8.8 | **9.01** |
| **`global_with_evidence`** (Combined) | **8.8 ms** | 8.0 | 4,838 chars | **10.0** | **9.0** | **9.62** |
| **`text_map_reduce`** (Naive Text MR) | 798 ms | 6.0 | 4,903 chars | 9.2 | 8.5 | **9.03** |

---

## Repository Structure

```
.
├── src/
│   ├── config.py                  # FalkorDB & environment configuration
│   ├── clustering_projection.py   # Substantive entity-to-entity graph projection
│   ├── community_detector.py      # Recursive hierarchical Leiden algorithm
│   ├── falkordb_community_ingest.py # Cypher ingestion for :Community & :CommunityReport
│   ├── report_context_builder.py  # Budget-aware element packing & qualifier bundling
│   ├── report_generator.py        # Structured community report generation
│   ├── global_search.py           # Global map-reduce query engine
│   ├── rag_pipeline.py            # Unified 4-mode RAG engine with auto-routing
│   ├── baseline_retriever.py      # BM25 + dense vector hybrid retriever
│   ├── graph_retriever.py         # FalkorDB local graph retriever
│   └── global_evaluate.py         # 6-mode comparative evaluation harness
├── tests/
│   └── test_stage3_failure_cases.py # 17 failure modes verification suite
├── data/
│   ├── snapshots/                 # Cryptographic snapshot manifest
│   ├── community_reports/         # Generated reports, manifests, inspection metrics
│   └── global_benchmark_questions.json # 5 global thematic evaluation questions
├── requirements.txt               # Dependencies
└── README.md                      # This document
```

---

## Installation & Quickstart

### Prerequisites
- Python 3.11+
- FalkorDB instance running on `127.0.0.1:6379` (e.g. via Docker or WSL)

### 1. Setup Virtual Environment
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Run Failure Verification Suite
```bash
python tests/test_stage3_failure_cases.py
```

### 3. Query the Legal RAG Pipeline
```python
from src.rag_pipeline import LegalRAGPipeline

pipeline = LegalRAGPipeline(regulator="RBI", enable_graph=True)
pipeline.initialize()

# Query with combined evidence grounding
result = pipeline.retrieve(
    "Across the NBFC regulatory framework, what recurring governance mandates and Board-level committee requirements apply?",
    mode="global_with_evidence"
)

print("Answer:", result["answer"])
print("Citations:", len(result["citations"]))
```

---

## License
MIT License. Developed for advanced regulatory analysis and legal knowledge graph research.
