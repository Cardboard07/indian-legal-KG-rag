import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
conn = sqlite3.connect("data/extraction_store.db")
c = conn.cursor()

c.execute("""
    SELECT document_id, document_title, total_chunks, doc_stem
    FROM documents
    WHERE regulator = 'RBI' AND (
        document_title LIKE '%Scale%' OR
        document_title LIKE '%SBR%' OR
        document_title LIKE '%Scale-based%' OR
        doc_stem LIKE '%Scale%' OR
        doc_stem LIKE '%SBR%'
    );
""")
rows = c.fetchall()
print(f"Found {len(rows)} SBR documents:")
for r in rows:
    print(f"  [{r[0]}] ({r[2]} chunks) {r[1] or r[3]}")
