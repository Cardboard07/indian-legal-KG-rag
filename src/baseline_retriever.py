"""Production Baseline Hybrid Retriever combining native SQLite FTS5 Okapi BM25
and SentenceTransformer dense semantic bi-encoder scoring with Reciprocal Rank Fusion (RRF, k=60).
Uses the persistent index database data/indexes/baseline/rbi_fts.db.
"""

import os
import re
import sys
import time
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
import numpy as np

from sentence_transformers import SentenceTransformer
from src.config import DATA_DIR, DB_PATH

sys.stdout.reconfigure(encoding="utf-8")

FTS_DB_PATH = DATA_DIR / "indexes" / "baseline" / "rbi_fts.db"
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
RRF_K = 60


def reciprocal_rank_fusion(
    bm25_ranks: List[str],
    dense_ranks: List[str],
    k: int = RRF_K
) -> List[Tuple[str, float]]:
    """Calculates RRF fused scores for ranked chunk IDs."""
    rrf_scores: Dict[str, float] = {}
    for rank, doc_id in enumerate(bm25_ranks):
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (k + rank + 1))
    for rank, doc_id in enumerate(dense_ranks):
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (k + rank + 1))
    return sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)


class BaselineHybridRetriever:
    """Production baseline hybrid retriever (FTS5 BM25 + Dense Bi-encoder + RRF)."""

    def __init__(
        self,
        fts_db_path: Path = FTS_DB_PATH,
        model_name: str = EMBEDDING_MODEL_NAME,
        regulator: str = "RBI",
    ):
        self.fts_db_path = Path(fts_db_path)
        self.model_name = model_name
        self.regulator = regulator
        self._model: Optional[SentenceTransformer] = None

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            print(f"Loading embedding model '{self.model_name}'...", flush=True)
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def build_or_load_index(self, force_rebuild: bool = False):
        """Verifies that the FTS5 index database exists."""
        if not self.fts_db_path.exists():
            raise FileNotFoundError(f"FTS5 database not found at {self.fts_db_path}. Run test_fts_setup.py first.")
        print(f"Verified FTS5 BM25 index database: {self.fts_db_path}", flush=True)

    def search(
        self,
        query: str,
        top_k: int = 5,
        candidate_pool_size: int = 40,
    ) -> List[Dict[str, Any]]:
        """Executes BM25 search via SQLite FTS5, scores candidates with dense bi-encoder, and fuses via RRF."""
        t0 = time.time()
        conn = sqlite3.connect(str(self.fts_db_path))
        c = conn.cursor()

        # Sanitize query for FTS5 boolean OR
        tokens = re.findall(r"\b[A-Za-z0-9\-]{2,}\b", query)
        filtered = [f'"{w}"' for w in tokens if len(w) > 2]
        if not filtered:
            filtered = [f'"{w}"' for w in tokens]
        fts_query = " OR ".join(filtered)

        c.execute("""
            SELECT chunk_id, document_id, section_locator, bm25(chunks_fts) as score, content
            FROM chunks_fts
            WHERE chunks_fts MATCH ?
            ORDER BY score ASC
            LIMIT ?;
        """, (fts_query, candidate_pool_size))
        rows = c.fetchall()
        conn.close()

        if not rows:
            return []

        # 1. BM25 Ranking
        # Note: FTS5 bm25() returns negative score where more negative = higher relevance
        bm25_ranks = [r[0] for r in rows]

        # 2. Dense Semantic Scoring over Candidate Pool
        candidate_texts = [r[4][:1200] for r in rows]
        q_emb = self.model.encode([query], normalize_embeddings=True)
        doc_embs = self.model.encode(candidate_texts, batch_size=32, normalize_embeddings=True)

        dense_scores = np.dot(doc_embs, q_emb[0])
        dense_order = np.argsort(dense_scores)[::-1]
        dense_ranks = [rows[i][0] for i in dense_order]

        # 3. Reciprocal Rank Fusion
        fused = reciprocal_rank_fusion(bm25_ranks, dense_ranks, k=RRF_K)
        top_fused = fused[:top_k]

        # Map back to chunk rows
        row_map = {r[0]: r for r in rows}
        results = []
        for rank, (cid, rrf_score) in enumerate(top_fused, 1):
            r = row_map[cid]
            full_text = r[4]
            results.append({
                "rank": rank,
                "chunk_id": cid,
                "document_id": r[1],
                "section_locator": r[2] or "Unspecified Section",
                "rrf_score": round(rrf_score, 6),
                "text": full_text,
                "preview": full_text[:300],
            })

        return results


if __name__ == "__main__":
    retriever = BaselineHybridRetriever()
    retriever.build_or_load_index()
    q = "If our standalone asset size is ₹600 crore, but our group NBFCs aggregate to ₹1,200 crore, are we treated as Base Layer (NBFC-BL) or Middle Layer (NBFC-ML)?"
    t0 = time.time()
    res = retriever.search(q, top_k=5)
    elapsed = time.time() - t0
    print(f"\nTop {len(res)} Hybrid Candidates ({elapsed*1000:.1f}ms):")
    for r in res:
        print(f"[{r['rank']}] RRF Score: {r['rrf_score']} | Locator: {r['section_locator']} | Chunk: {r['chunk_id']}")
        print(f"     Preview: {r['preview'][:140]}...\n")
