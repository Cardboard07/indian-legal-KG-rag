import os
import sys
import json

sys.path.insert(0, os.path.abspath("."))
from src.config import get_falkordb_client, FALKORDB_GRAPH_NAME

client = get_falkordb_client()
graph = client.select_graph(FALKORDB_GRAPH_NAME)

print("--- Clustering Projection Inspection ---")
res = graph.query("""
MATCH (s:Entity)-[r:RELATION]->(t:Entity)
RETURN s.entity_type, r.rel_type, t.entity_type, count(r) as cnt
ORDER BY cnt DESC
LIMIT 25
""")
for row in res.result_set:
    print(f"{row[0]} -[{row[1]}]-> {row[2]}: {row[3]}")

res_req = graph.query("MATCH (e:Entity {entity_type: 'Requirement'})-[r:RELATION]-() RETURN count(distinct e)")
print(f"\nConnected Requirement entities: {res_req.result_set[0][0]}")

res_isolates = graph.query("MATCH (e:Entity) WHERE NOT (e)-[:RELATION]-() RETURN e.entity_type, count(e)")
print(f"\nIsolated entities by type (no RELATION edges):")
for row in res_isolates.result_set:
    print(f"  {row[0]}: {row[1]}")
