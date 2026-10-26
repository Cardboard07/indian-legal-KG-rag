# Indian Legal KG-RAG: Hierarchical Community GraphRAG for Indian Financial Regulations

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Graph Database](https://img.shields.io/badge/database-FalkorDB_4.18-red.svg)](https://www.falkordb.com/)
[![Reference Architecture](https://img.shields.io/badge/architecture-Microsoft_GraphRAG-purple.svg)](https://arxiv.org/abs/2404.16130)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Refinement](https://img.shields.io/badge/quality-cluster--refined_&_audited-brightgreen.svg)]()

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
                             [Multi-GPU Cluster Audit & Refinement]
                             [3,996 Triplets Audited / 831 Quarantined]
                                             │
                                             ▼
                      ┌───────────────────────────────────────────────┐
                      │   Stage 2: Refined & Verified FalkorDB KG     │
                      │  (321k+ Nodes, 702k+ Edges, Vector + BM25)    │
                      └──────────────────────┬────────────────────────┘
                                             │
                             [Substantive Clustering Projection]
                             (28,246 Connected Nodes / 42,236 Edges)
                                             │
                                             ▼
                      ┌───────────────────────────────────────────────┐
                      │ Stage 3: Hierarchical Leiden Detection        │
                      │  (5,042 Communities, 3-Level Acyclic Tree)    │
                      └──────────────────────┬────────────────────────┘
                                             │
                             [Bottom-Up Degree Element Packing]
                                             │
                                             ▼
                      ┌───────────────────────────────────────────────┐
                      │  Stage 3: Evidence-Grounded Community Reports │
                      │  (287 Synthesized Domain & Sub-Domain Reports)│
                      └──────────────────────┬────────────────────────┘
                                             │
                                             ▼
                      ┌───────────────────────────────────────────────┐
                      │   Global Map-Reduce Question Answering        │
                      │   (Helpfulness 0-100, Bounded Reduction)      │
                      └───────────────────────────────────────────────┘
```

---

## Key System Pillars

### 1. Multi-GPU Cluster-Based KG Refinement & Verification
- **High-Precision Batch Audit**: Evaluated 1,553 chunks and audited 3,996 extracted candidate triplets using a distributed multi-GPU cluster.
- **Strict Quality Control**:
  - **2,197 Valid Triplets**: Fully verified against source ground truth.
  - **968 Distorted Triplets Repaired**: Normalized relation types and canonicalized entity boundaries via structured LLM proposal contracts.
  - **831 Quarantined Invalid Triplets**: Distortions, hallucinated relations, and speculative links are strictly quarantined and 100% excluded from FalkorDB ingestion.
  - **1,781 New Grounded Discoveries**: High-salience missing regulatory triplets discovered and verified verbatim against source text.
  - **Context-Aware Pass 2**: 100% of context-dependent circular cross-references resolved.
- **Three-Tiered Immutability Safeguards**:
  1. *Online DB Backup*: Full byte-for-byte physical backup created via SQLite Online Backup API ([`data/extraction_store_pre_refinement.db`](data/extraction_store_pre_refinement.db)).
  2. *Immutable Snapshot Tables*: `relationships_pre_refinement`, `entities_pre_refinement`, and `evidence_spans_pre_refinement` tables locked with 9 database-level SQLite `ABORT` triggers.
  3. *Granular Audit Trail*: Complete triplet-level audit log with JSON before/after state diffs (`refinement_audits` with 5,777 records).

### 2. Explicit Substantive Clustering Projection
- Filters out administrative and bookkeeping edges (`APPEARS_IN`, `BASED_ON`, and star-forming `Authority: RBI` `ISSUES` edges) to prevent giant-cluster collapse.
- Weights edges by deduplicated supporting passage frequency ($w \ge 1.0$).
- Projections yield **28,246 connected entities** and **42,236 substantive edges**.

### 3. Hierarchical Leiden Community Detection
- **5,042 total communities** across a 3-level acyclic hierarchy:
  - **Level 0 (Root Domains):** 237 distinct regulatory frameworks (e.g. Housing Finance Companies, NBFC Systemically Important Directions, Primary Dealers, Commercial Banks).
  - **Level 1 (Sub-Frameworks):** 2,342 thematic sub-domains (Capital Adequacy, Liquidity Risk, Stressed Assets).
  - **Level 2 (Operational Clusters):** 2,462 leaf rule sets.
- **100% frontier coverage** across all 3 levels with 0 gaps and 0 node overlaps.

### 4. Legal Exception & Qualifier Bundling
- Bottom-up element packing prioritized by combined degree $(deg(u) + deg(v))$.
- Preserves legal modality: statutory obligations are strictly bundled with their associated conditions, exceptions, and numeric thresholds.
- Synthesized **287 structured community reports** (50 prominent Level 1 sub-domain reports + all 237 Level 0 root framework reports) persisted directly as `:CommunityReport` nodes in FalkorDB.

### 5. 4-Mode Unified Retrieval Engine
- `baseline_hybrid`: BM25 lexical + dense vector search (all-MiniLM-L6-v2).
- `hybrid_graph`: Personalized PageRank (PPR) local graph expansion in FalkorDB.
- `global_community`: Hierarchical map-reduce search across community report frontiers (`L0` and `L1`).
- `global_with_evidence`: Community thematic mapping with transitive resolution to verbatim source passage quotes.
- `auto`: Intelligent query classifier routing specific section queries to local search and corpus-wide themes to global search.

### 6. Full Citation Chain Traceability
Every generated answer traces its provenance through an unbroken chain:
$$\text{Answer Claim} \longrightarrow \text{Map Point} \longrightarrow \text{Report Finding} \longrightarrow \text{Assertion ID} \longrightarrow \text{Evidence Span} \longrightarrow \text{Source Chunk/Doc}$$

### 7. Dual Test Verification Suites (33/33 Tests Passing)
- **17-Point Stage 3 Failure Suite** ([`tests/test_stage3_failure_cases.py`](tests/test_stage3_failure_cases.py)): Verifies empty projections, isolates, non-negative weights, unsplittable partitions, context budgets, exception preservation, all-zero map resilience, and idempotency.
- **16-Point Refinement Safeguards Suite** ([`tests/test_refinement.py`](tests/test_refinement.py)): Verifies SQLite backups, trigger enforcement, proposal validation, and audit trail fidelity.

---

## Empirical Benchmark Results

Evaluated on 5 comprehensive global regulatory questions across 6 retrieval modes under an identical 5,000-character context budget:

| Retrieval Mode | Avg Latency | Avg Citations | Context Packed | Comprehensiveness | Directness | Overall Score (/10) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`baseline_hybrid`** (Lexical + Vector) | 2,088.5 ms | 5.0 | 1,784 chars | 6.4 | 8.5 | **7.86** |
| **`hybrid_graph`** (Stage 2 Local PPR) | 1,682.7 ms | 8.0 | 4,998 chars | 10.0 | 9.0 | **9.62** |
| **`global_community_l0`** (Root Reports) | **10.0 ms** | **8.0** | **4,839 chars** | **10.0** | **9.0** | **9.62** |
| **`global_community_l1`** (Sub-Reports) | **8.3 ms** | **8.0** | **4,253 chars** | **9.8** | **9.0** | **9.58** |
| **`global_with_evidence`** (Combined) | **10.1 ms** | **8.0** | **4,839 chars** | **10.0** | **9.0** | **9.62** |
| **`text_map_reduce`** (Naive Text MR) | 737.1 ms | 6.0 | 4,903 chars | 9.2 | 8.5 | **9.03** |

*Key Benchmark Takeaways:*
- **Quality Improvement Post-Refinement**: `global_community_l1` citation count increased from **7.0 $\rightarrow$ 8.0** and overall quality score improved from **9.01 $\rightarrow$ 9.58**, driven by the removal of 831 quarantined relations and incorporation of 1,781 grounded discoveries.
- **Latency Advantage**: Global hierarchical graph search is **~73x faster** than naive text map-reduce (8–10 ms vs 737 ms) while delivering strictly grounded answers.

---

## Graph Topology Evolution & Snapshot Tracking

Cryptographic snapshot manifests in [`data/snapshots/`](data/snapshots/) enable exact version comparison:

| Topology Metric | Pre-Refinement Baseline ([`snapshot_v1`](data/snapshots/snapshot_v1_manifest.json)) | Refined Graph ([`snapshot_v2_refined`](data/snapshots/snapshot_v2_refined_manifest.json)) | Net Change |
| :--- | :--- | :--- | :--- |
| **Total Graph Nodes** | 311,210 | 321,083 | **+9,873** |
| **Total Graph Edges** | 624,889 | 702,044 | **+77,155** |
| **Canonical Entities** | 157,589 | 159,402 | **+1,813** |
| **Relationship Assertions** | 78,185 | 79,135 | **+950** *(net of 831 quarantined)* |
| **Evidence Spans** | 40,348 | 42,129 | **+1,781** *(grounded passages)* |
| **Connected Entities** | 26,694 | 28,246 | **+1,552** |
| **Substantive Edges** | 39,812 | 42,236 | **+2,424** |
| **Total Communities** | 4,920 | 5,042 | **+122** |
| **Synthesized Reports** | 195 | 287 | **+92** |
| **Database SHA-256** | `df7b3ceca770fe5dc58b...` | `2abb1a8fbb2c1205eda7...` | *Durable version hash* |

---

## Repository Structure

```
.
├── src/
│   ├── config.py                  # FalkorDB & environment configuration
│   ├── refinement_contract.py     # Pydantic/pure-Python repair & audit contracts
│   ├── clustering_projection.py   # Substantive entity-to-entity graph projection
│   ├── community_detector.py      # Recursive hierarchical Leiden algorithm
│   ├── falkordb_ingest.py         # FalkorDB Cypher ingestion engine (with quarantine filter)
│   ├── falkordb_community_ingest.py # Cypher ingestion for :Community & :CommunityReport
│   ├── report_context_builder.py  # Budget-aware element packing & qualifier bundling
│   ├── report_generator.py        # Structured community report generation
│   ├── global_search.py           # Global map-reduce query engine
│   ├── rag_pipeline.py            # Unified 4-mode RAG engine with auto-routing
│   ├── baseline_retriever.py      # BM25 + dense vector hybrid retriever
│   ├── graph_retriever.py         # FalkorDB local graph retriever
│   ├── freeze_snapshot.py         # Snapshot manifest freezing & hashing
│   └── global_evaluate.py         # 6-mode comparative evaluation harness
├── scripts/
│   ├── run_all_batches.py         # Multi-GPU batch queue runner
│   ├── refine_context_pass.py     # Pass 2 context-dependent resolution engine
│   └── reconcile_refinement_results.py # SQLite reconciliation & safeguard installer
├── tests/
│   ├── test_refinement.py         # 16-point refinement & safeguards unit test suite
│   └── test_stage3_failure_cases.py # 17-point Stage 3 failure & integrity test suite
├── data/
│   ├── snapshots/                 # Cryptographic snapshot manifests (v1, v2_refined)
│   ├── community_reports/         # Generated reports, manifests, inspection metrics
│   ├── community_reports_backup_pre_refinement/ # Preserved pre-refinement reports
│   └── global_benchmark_questions.json # 5 global thematic evaluation questions
├── requirements.txt               # Dependencies
└── README.md                      # This document
```

---

## Quickstart & Reproduction

### 1. Setup Environment
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Run Verification Suites (33 Tests)
```bash
# 1. Run Refinement Safeguards & Pipeline Tests
python tests/test_refinement.py

# 2. Run Stage 3 Failure & Integrity Tests
python tests/test_stage3_failure_cases.py
```

### 3. Rebuild Knowledge Graph & Communities
```bash
# 1. Ingest clean refined graph into FalkorDB
python src/falkordb_ingest.py --regulator RBI --drop-existing

# 2. Run Hierarchical Leiden Community Detection
python src/community_detector.py

# 3. Ingest Communities into FalkorDB
python src/falkordb_community_ingest.py

# 4. Generate Community Reports
python src/report_generator.py --reset
```

### 4. Execute Benchmark
```bash
python src/global_evaluate.py
```

### 5. Query the Legal RAG Pipeline
```python
from src.rag_pipeline import LegalRAGPipeline

pipeline = LegalRAGPipeline(regulator="RBI", enable_graph=True)
pipeline.initialize()

# Query with combined hierarchical evidence grounding
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
