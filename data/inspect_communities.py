import json

with open("data/community_reports/communities.json", "r", encoding="utf-8") as f:
    comms = json.load(f)

l0 = [c for c in comms.values() if c["level"] == 0 and not c["id"].endswith("ISOLATES")]
l0.sort(key=lambda x: x["size"], reverse=True)

print(f"Total Level 0 communities: {len(l0)}")
for i, c in enumerate(l0[:20], 1):
    reps = ", ".join(c["representative_entities"][:3])
    print(f"[{i}] {c['id']}: size={c['size']}, internal_edges={c['internal_edge_count']}, chunks={c['associated_chunk_count']}, children={len(c['children_ids'])}")
    print(f"    Reps: {reps}")
