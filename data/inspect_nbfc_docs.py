import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
conn = sqlite3.connect("data/extraction_store.db")
c = conn.cursor()

c.execute("""
    SELECT document_id, document_title, total_chunks, doc_stem
    FROM documents
    WHERE regulator = 'RBI' AND (
        document_title LIKE '%NBFC%' OR
        document_title LIKE '%Scale Based%' OR
        document_title LIKE '%Non-Banking%' OR
        document_title LIKE '%Master Direction%' OR
        doc_stem LIKE '%NBFC%' OR
        doc_stem LIKE '%Scale%'
    )
    ORDER BY total_chunks DESC;
""")
rows = c.fetchall()
print(f"Found {len(rows)} matching RBI NBFC/Direction documents:")
for r in rows[:20]:
    print(f"  [{r[0]}] ({r[2]} chunks) {r[1] or r[3]}")
