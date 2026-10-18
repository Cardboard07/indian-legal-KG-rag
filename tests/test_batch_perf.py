import time
from src.config import get_falkordb_client

client = get_falkordb_client()
graph = client.select_graph("test_perf")

try:
    graph.query("CREATE INDEX FOR (e:TestEntity) ON (e.id)")
except Exception as ex:
    print("Index:", ex)

batch = [{"id": f"E_{i}", "name": f"Entity {i}", "type": "Authority"} for i in range(1000)]
t0 = time.time()
graph.query(
    "UNWIND $batch AS item MERGE (e:TestEntity {id: item.id}) SET e.name = item.name, e.type = item.type",
    {"batch": batch}
)
t1 = time.time()
rate = 1000 / (t1 - t0)
print(f"1000 UNWIND MERGE queries executed in {t1 - t0:.3f}s ({rate:.0f} nodes/sec)!")

# Test read
res = graph.query("MATCH (e:TestEntity) RETURN count(e)")
print("Total test nodes in graph:", res.result_set[0][0])

client.delete_graph("test_perf")
print("Cleaned up test_perf graph successfully.")
