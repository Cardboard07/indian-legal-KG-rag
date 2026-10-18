import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
conn = sqlite3.connect("data/extraction_store.db")
c = conn.cursor()

c.execute("""
    SELECT canonical_name, entity_type, COUNT(*) as cnt
    FROM entities
    WHERE canonical_name LIKE '%Base Layer%' OR
          canonical_name LIKE '%Middle Layer%' OR
          canonical_name LIKE '%NBFC-BL%' OR
          canonical_name LIKE '%NBFC-ML%' OR
          canonical_name LIKE '%Chief Compliance Officer%' OR
          canonical_name LIKE '%CCO%'
    GROUP BY canonical_name, entity_type
    ORDER BY cnt DESC
    LIMIT 20;
""")
rows = c.fetchall()
print("Matching Entities in extraction store:")
for r in rows:
    print(f"  {r[0]} ({r[1]}): {r[2]} mentions")
