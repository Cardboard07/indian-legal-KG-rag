import os
import time
import sqlite3
from src.config import DB_PATH, DATA_DIR

fts_db_path = DATA_DIR / "indexes" / "baseline" / "rbi_fts.db"
fts_db_path.parent.mkdir(parents=True, exist_ok=True)

src_conn = sqlite3.connect(DB_PATH)
src_c = src_conn.cursor()
src_c.execute("SELECT chunk_id, file_path, document_id, section_locator FROM manifest_chunks WHERE regulator = 'RBI';")
rows = src_c.fetchall()
print(f"Total RBI chunks to index into FTS5: {len(rows)}")

dest_conn = sqlite3.connect(str(fts_db_path))
dest_c = dest_conn.cursor()
dest_c.execute("DROP TABLE IF EXISTS chunks_fts;")
dest_c.execute("""
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    chunk_id UNINDEXED,
    document_id UNINDEXED,
    section_locator UNINDEXED,
    content
);
""")

t0 = time.time()
batch = []
for i, (cid, path, did, loc) in enumerate(rows):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fp:
            text = fp.read()
    except Exception:
        text = ""
    batch.append((cid, did, loc or "", text))
    if len(batch) >= 2000:
        dest_c.executemany("INSERT INTO chunks_fts(chunk_id, document_id, section_locator, content) VALUES (?, ?, ?, ?);", batch)
        dest_conn.commit()
        batch = []
        print(f"Indexed {i+1}/{len(rows)} chunks in {time.time()-t0:.2f}s...")

if batch:
    dest_c.executemany("INSERT INTO chunks_fts(chunk_id, document_id, section_locator, content) VALUES (?, ?, ?, ?);", batch)
    dest_conn.commit()

dest_conn.close()
elapsed = time.time() - t0
print(f"All {len(rows)} RBI chunks indexed into SQLite FTS5 in {elapsed:.2f}s ({len(rows)/elapsed:.0f} chunks/sec)!")

# Test BM25 query
test_conn = sqlite3.connect(str(fts_db_path))
test_c = test_conn.cursor()
query = "standalone asset size 600 crore group aggregate 1200 crore Base Layer Middle Layer"
# sanitize query for fts5
fts_query = " OR ".join([f'"{w}"' for w in query.split() if len(w) > 3])
t_q = time.time()
test_c.execute(f"""
    SELECT chunk_id, document_id, section_locator, bm25(chunks_fts) as score, substr(content, 1, 200)
    FROM chunks_fts
    WHERE chunks_fts MATCH ?
    ORDER BY score ASC
    LIMIT 5;
""", (fts_query,))
results = test_c.fetchall()
q_time = (time.time() - t_q) * 1000
print(f"\nFTS5 Okapi BM25 Search executed in {q_time:.2f}ms! Top results:")
for r in results:
    print(f"  Chunk: {r[0]} | Score: {r[3]:.4f} | Locator: {r[2]}")
    print(f"    Snippet: {r[4][:120]}...\n")
test_conn.close()
