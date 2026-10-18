from src.config import get_falkordb_client, FALKORDB_GRAPH_NAME

c = get_falkordb_client()
g = c.select_graph(FALKORDB_GRAPH_NAME)
res_before = g.query("MATCH (n) RETURN count(n)").result_set[0][0]
edges_before = g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
print(f"Before idempotent test: {res_before} nodes, {edges_before} edges")

test_batch = [{
    "id": "CANONICAL::Authority::reserve bank of india",
    "name": "Reserve Bank of India",
    "type": "Authority",
    "scope": "",
    "description": "Central bank and monetary authority of India",
    "mention_count": 34731,
    "aliases": "[]",
    "review_status": "unreviewed"
}]
cypher = """
UNWIND $batch AS item
MERGE (e:Entity {id: item.id})
ON CREATE SET e.canonical_name = item.name
ON MATCH SET e.canonical_name = item.name
"""
g.query(cypher, {"batch": test_batch})

res_after = g.query("MATCH (n) RETURN count(n)").result_set[0][0]
edges_after = g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
print(f"After idempotent test: {res_after} nodes, {edges_after} edges")

assert res_before == res_after, f"Node count changed from {res_before} to {res_after}!"
assert edges_before == edges_after, f"Edge count changed from {edges_before} to {edges_after}!"
print("IDEMPOTENCY TEST PASSED: No duplicate nodes or edges created!")
