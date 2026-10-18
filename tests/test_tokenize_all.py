import time
import sqlite3
from rank_bm25 import BM25Okapi
from src.config import DB_PATH
from src.baseline_retriever import simple_tokenize

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute("SELECT chunk_id, file_path FROM manifest_chunks WHERE regulator = 'RBI';")
rows = c.fetchall()

t0 = time.time()
corpus = []
for i, (cid, path) in enumerate(rows):
    with open(path, "r", encoding="utf-8", errors="replace") as fp:
        text = fp.read()
    tokens = text.lower().split()
    corpus.append(tokens)
    if (i + 1) % 10000 == 0:
        print(f"Tokenized {i+1}/{len(rows)} chunks in {time.time()-t0:.2f}s...")

print(f"Tokenized all {len(corpus)} chunks in {time.time()-t0:.2f}s.")

t1 = time.time()
print("Building BM25Okapi...")
bm25 = BM25Okapi(corpus)
print(f"BM25Okapi created in {time.time()-t1:.2f}s!")

# Test query
q_tokens = "standalone asset size 600 crore group aggregate 1200 crore Base Layer Middle Layer".lower().split()
scores = bm25.get_scores(q_tokens)
top_idx = scores.argsort()[::-1][:5]
print("Top 5 BM25 matches:")
for idx in top_idx:
    print(f"  Chunk: {rows[idx][0]} | Score: {scores[idx]:.4f}")
