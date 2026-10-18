"""Report Context Builder for Legal GraphRAG Stage 3.
Implements budget-aware, hierarchical element packing matching Microsoft GraphRAG Paper §2.4:
- Leaf packing prioritized by combined endpoint degree (deg(u) + deg(v))
- Legal adaptation: Preserves substantive requirements together with their conditions, exceptions, and numeric constraints
- Hierarchical compression: Compresses oversized parent communities using child community reports
- Generates durable per-report context manifests recording available vs included records and omissions
"""

import os
import sys
import json
import time
from typing import Dict, Any, List, Set, Optional, Tuple

sys.path.insert(0, os.path.abspath("."))
from src.config import get_falkordb_client, FALKORDB_GRAPH_NAME
from src.clustering_projection import ClusteringProjection


class ReportContextBuilder:
    """Builds prioritized, budget-aware context for community report generation."""

    def __init__(
        self,
        projection: ClusteringProjection,
        max_context_chars: int = 12000,
        graph_name: str = FALKORDB_GRAPH_NAME,
    ):
        self.projection = projection
        self.max_context_chars = max_context_chars
        self.graph_name = graph_name
        self.client = get_falkordb_client()
        self.graph = self.client.select_graph(graph_name)

        # Pre-cache assertion details from FalkorDB for rapid lookup
        self.assertion_cache: Dict[str, Dict[str, Any]] = {}
        self._load_assertion_cache()

    def _load_assertion_cache(self):
        """Pre-loads assertions with qualifiers and chunks from FalkorDB."""
        print("Pre-caching assertions and qualifiers from FalkorDB...", flush=True)
        cypher = """
        MATCH (a:RelationshipAssertion)
        RETURN a.id, a.relationship_type, a.description, a.conditions_json, a.exceptions_json,
               a.numeric_constraints_json, a.temporal_qualifiers_json, a.is_conditional, a.chunk_id
        """
        res = self.graph.query(cypher)
        for row in res.result_set:
            aid, rtype, desc, conds, excs, nums, temps, is_cond, cid = (
                row[0], row[1], row[2], row[3], row[4], row[5], row[6], bool(row[7]), row[8]
            )
            self.assertion_cache[aid] = {
                "id": aid,
                "type": rtype,
                "description": desc or "",
                "conditions": json.loads(conds) if conds else [],
                "exceptions": json.loads(excs) if excs else [],
                "numeric_constraints": json.loads(nums) if nums else [],
                "temporal_qualifiers": json.loads(temps) if temps else [],
                "is_conditional": is_cond,
                "chunk_id": cid or "",
            }
        print(f"Cached {len(self.assertion_cache)} assertions.", flush=True)

    def build_context(
        self,
        community: Dict[str, Any],
        child_reports: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Constructs prioritized context string and manifest for a given community."""
        cid = community["id"]
        level = community["level"]
        members = set(community.get("member_entity_ids", []))
        entities_dict = self.projection.entities
        edge_dict = self.projection.edges

        manifest = {
            "community_id": cid,
            "level": level,
            "title": community.get("title", ""),
            "entities_available": len(members),
            "entities_included": 0,
            "assertions_available": 0,
            "assertions_included": 0,
            "assertions_omitted": 0,
            "child_reports_used": [],
            "conditions_preserved": 0,
            "exceptions_preserved": 0,
            "numeric_constraints_preserved": 0,
            "context_char_count": 0,
        }

        context_sections = []
        context_sections.append(f"=== COMMUNITY: {cid} (Level {level}) ===")
        context_sections.append(f"Title: {community.get('title', '')}")
        context_sections.append(f"Member Entities Count: {len(members)}")

        # 1. Hierarchical Compression: If parent has child reports, pack child reports first
        if child_reports:
            child_ids = community.get("children_ids", [])
            usable_child_reports = [child_reports[ch_id] for ch_id in child_ids if ch_id in child_reports]
            if usable_child_reports:
                context_sections.append("\n--- SUB-COMMUNITY SUMMARIES (Hierarchical Context) ---")
                for ch_rep in usable_child_reports:
                    manifest["child_reports_used"].append(ch_rep.get("community_id", ""))
                    ch_summary = (
                        f"Sub-Community: {ch_rep.get('title', 'Untitled')}\n"
                        f"Summary: {ch_rep.get('summary', '')}\n"
                        f"Key Findings: {'; '.join([f.get('summary', '') for f in ch_rep.get('findings', [])[:3]])}\n"
                    )
                    context_sections.append(ch_summary)

        # 2. Extract and Rank Internal Relationships by Combined Endpoint Degree (Paper §2.4)
        internal_edges = []
        for (u, v), edata in edge_dict.items():
            if u in members and v in members:
                # Combined endpoint degree
                deg_sum = entities_dict.get(u, {}).get("degree", 0) + entities_dict.get(v, {}).get("degree", 0)
                internal_edges.append((deg_sum, u, v, edata))

        # Sort descending by combined degree
        internal_edges.sort(key=lambda x: x[0], reverse=True)
        manifest["assertions_available"] = sum(len(e[3]["assertion_ids"]) for e in internal_edges)

        # 3. Pack Entities & Qualified Assertions
        context_sections.append("\n--- PROMINENT ENTITIES ---")
        sorted_members = sorted(
            list(members),
            key=lambda e: entities_dict.get(e, {}).get("degree", 0),
            reverse=True
        )

        current_chars = sum(len(s) for s in context_sections)
        included_entities = set()

        # Add top entities that fit budget (cap at top 30 entities)
        max_entities_to_list = 30
        for eid in sorted_members[:max_entities_to_list]:
            edata = entities_dict.get(eid, {})
            eline = f"- {edata.get('name', eid)} ({edata.get('type', 'Entity')}) [Degree: {edata.get('degree', 0)}]"
            context_sections.append(eline)
            current_chars += len(eline)
            included_entities.add(eid)
            manifest["entities_included"] += 1

        context_sections.append("\n--- SUBSTANTIVE REGULATORY RULES & ASSERTIONS ---")
        for deg_sum, u, v, edata in internal_edges:
            u_name = entities_dict.get(u, {}).get("name", u)
            v_name = entities_dict.get(v, {}).get("name", v)

            for aid in edata["assertion_ids"]:
                ainfo = self.assertion_cache.get(aid)
                if not ainfo:
                    continue

                # Format assertion with preserved qualifiers
                conds = ainfo["conditions"]
                excs = ainfo["exceptions"]
                nums = ainfo["numeric_constraints"]
                qual_str = ""
                if conds:
                    qual_str += f" | Conditions: {conds}"
                    manifest["conditions_preserved"] += len(conds)
                if excs:
                    qual_str += f" | Exceptions: {excs}"
                    manifest["exceptions_preserved"] += len(excs)
                if nums:
                    qual_str += f" | Thresholds: {nums}"
                    manifest["numeric_constraints_preserved"] += len(nums)

                desc = ainfo["description"] or f"{u_name} {ainfo['type']} {v_name}"
                cid = ainfo["chunk_id"]

                rule_entry = (
                    f"Assertion [{aid}]: {desc}{qual_str}\n"
                    f"  Source: {u_name} -> {ainfo['type']} -> {v_name} | Chunk: {cid}\n"
                )

                if current_chars + len(rule_entry) < self.max_context_chars:
                    context_sections.append(rule_entry)
                    current_chars += len(rule_entry)
                    manifest["assertions_included"] += 1
                else:
                    manifest["assertions_omitted"] += 1

        full_context_text = "\n".join(context_sections)
        manifest["context_char_count"] = len(full_context_text)

        return full_context_text, manifest


if __name__ == "__main__":
    proj = ClusteringProjection()
    proj.build_projection()

    with open("data/community_reports/communities.json", "r", encoding="utf-8") as f:
        comms = json.load(f)

    builder = ReportContextBuilder(projection=proj)
    # Test on a prominent community
    test_comm = comms["COMM::v1::L0::1"]
    ctx, manifest = builder.build_context(test_comm)

    print(f"\n--- Context Built for {test_comm['id']} ---")
    print(f"Context Length: {len(ctx)} chars")
    print("Manifest:", json.dumps(manifest, indent=2))
    print("\nContext Preview (first 1000 chars):\n", ctx[:1000])
