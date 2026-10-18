"""Compute Community Quality Metrics and Diagnostics for Stage 3 Inspection Artifact."""
import os
import json
from collections import Counter

def main():
    with open("data/community_reports/communities.json", "r", encoding="utf-8") as f:
        comms = json.load(f)
    with open("data/community_reports/reports.json", "r", encoding="utf-8") as f:
        reports = json.load(f)
    with open("data/community_reports/context_manifests.json", "r", encoding="utf-8") as f:
        manifests = json.load(f)

    # Level counts
    level_counts = Counter(c["level"] for c in comms.values())
    leaf_counts = Counter(c["is_leaf"] for c in comms.values())
    reasons = Counter(c.get("termination_reason") for c in comms.values() if c.get("is_leaf"))

    # Size stats by level
    sizes_l0 = [c["size"] for c in comms.values() if c["level"] == 0 and c["id"] != "COMM::v1::ISOLATES"]
    sizes_l1 = [c["size"] for c in comms.values() if c["level"] == 1]
    sizes_l2 = [c["size"] for c in comms.values() if c["level"] == 2]

    isolates_size = comms.get("COMM::v1::ISOLATES", {}).get("size", 0)

    # Boundary edges and internal edges
    internal_edges_l0 = [c.get("internal_edge_count", 0) for c in comms.values() if c["level"] == 0 and c["id"] != "COMM::v1::ISOLATES"]
    boundary_edges_l0 = [c.get("boundary_edge_count", 0) for c in comms.values() if c["level"] == 0 and c["id"] != "COMM::v1::ISOLATES"]

    # Top communities by size
    sorted_l0 = sorted([c for c in comms.values() if c["level"] == 0 and c["id"] != "COMM::v1::ISOLATES"], key=lambda x: x["size"], reverse=True)

    top_l0_summary = []
    for c in sorted_l0[:10]:
        cid = c["id"]
        rep = reports.get(cid, {})
        title = rep.get("title", "No Title")
        findings_cnt = len(rep.get("findings", []))
        top_entities = [e.get("name") if isinstance(e, dict) else e for e in c.get("top_entities", [])[:5]]
        top_l0_summary.append({
            "id": cid,
            "title": title,
            "size": c["size"],
            "internal_edges": c.get("internal_edge_count", 0),
            "boundary_edges": c.get("boundary_edge_count", 0),
            "findings_count": findings_cnt,
            "top_entities": top_entities
        })

    metrics = {
        "total_communities": len(comms),
        "level_counts": dict(level_counts),
        "leaf_counts": dict(leaf_counts),
        "termination_reasons": dict(reasons),
        "isolates_count": isolates_size,
        "l0_stats": {
            "count": len(sizes_l0),
            "min": min(sizes_l0) if sizes_l0 else 0,
            "max": max(sizes_l0) if sizes_l0 else 0,
            "mean": sum(sizes_l0) / len(sizes_l0) if sizes_l0 else 0,
            "median": sorted(sizes_l0)[len(sizes_l0)//2] if sizes_l0 else 0,
        },
        "l1_stats": {
            "count": len(sizes_l1),
            "min": min(sizes_l1) if sizes_l1 else 0,
            "max": max(sizes_l1) if sizes_l1 else 0,
            "mean": sum(sizes_l1) / len(sizes_l1) if sizes_l1 else 0,
            "median": sorted(sizes_l1)[len(sizes_l1)//2] if sizes_l1 else 0,
        },
        "l2_stats": {
            "count": len(sizes_l2),
            "min": min(sizes_l2) if sizes_l2 else 0,
            "max": max(sizes_l2) if sizes_l2 else 0,
            "mean": sum(sizes_l2) / len(sizes_l2) if sizes_l2 else 0,
            "median": sorted(sizes_l2)[len(sizes_l2)//2] if sizes_l2 else 0,
        },
        "total_reports": len(reports),
        "top_l0_communities": top_l0_summary
    }

    with open("data/community_reports/inspection_metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    print("Inspection metrics computed:")
    print(json.dumps(metrics, indent=2))

if __name__ == "__main__":
    main()
