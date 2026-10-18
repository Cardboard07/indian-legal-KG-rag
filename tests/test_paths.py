import time
import os
import sqlite3
from src.config import DB_PATH

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute("SELECT chunk_id, file_path FROM manifest_chunks WHERE regulator = 'RBI';")
rows = c.fetchall()
print(f"Total RBI chunk rows: {len(rows)}")

t0 = time.time()
missing = 0
found = 0
for i, (cid, path) in enumerate(rows):
    if not os.path.exists(path):
        missing += 1
    else:
        found += 1
    if (i + 1) % 5000 == 0:
        print(f"Checked {i+1}/{len(rows)} paths in {time.time()-t0:.2f}s (found: {found}, missing: {missing})")

print(f"Final: found {found}, missing {missing} in {time.time()-t0:.2f}s.")
