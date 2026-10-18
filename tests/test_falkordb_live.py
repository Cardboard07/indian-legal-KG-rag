import os
import sys
import subprocess
from falkordb import FalkorDB

def get_falkordb_host() -> str:
    env_host = os.getenv("FALKORDB_HOST")
    if env_host:
        return env_host

    # Try localhost first
    import socket
    for candidate in ["127.0.0.1", "localhost"]:
        try:
            s = socket.socket()
            s.settimeout(0.5)
            s.connect((candidate, 6379))
            s.close()
            return candidate
        except Exception:
            pass

    # Fall back to WSL IP
    try:
        out = subprocess.check_output(["wsl", "hostname", "-I"], text=True)
        wsl_ip = out.strip().split()[0]
        return wsl_ip
    except Exception:
        return "127.0.0.1"

host = get_falkordb_host()
print(f"Connecting to FalkorDB on {host}:6379...")
client = FalkorDB(host=host, port=6379)
graph = client.select_graph("test_live")
graph.query("CREATE (n:LiveNode {status: 'connected', test_id: 'test_123'})")
res = graph.query("MATCH (n:LiveNode) RETURN n.status, n.test_id")
print("Query Result Set:", res.result_set)
graph.query("MATCH (n:LiveNode) DELETE n")
print("FalkorDB is fully connected and working flawlessly!")
