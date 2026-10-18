import time
import sqlite3
from src.config import DB_PATH

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute("SELECT file_path FROM manifest_chunks WHERE regulator = 'RBI' LIMIT 1000;")
paths = [r[0] for r in c.fetchall()]

t0 = time.time()
found = 0
for p in paths:
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fp:
            _ = fp.read()
            found += 1
    except Exception:
        pass
elapsed = time.time() - t0
print(f"Read {found}/1000 files in {elapsed:.3f}s ({found/max(elapsed, 0.001):.0f} files/s)")
