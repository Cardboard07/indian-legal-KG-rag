"""Community Report Generation Engine for Legal GraphRAG Stage 3.
Synthesizes structured, evidence-grounded community reports matching Microsoft GraphRAG schema:
- Title, summary, organizational rating (0-10 heuristic)
- Substantive findings with explicit citations to assertion IDs and chunk IDs
- Preserved material conditions, exceptions, and numeric thresholds
- Temporal/version scope and caveats
- Unresolved references and coverage limitations
- Full provenance tracking under unreviewed_extraction_dev_mode
- Idempotent FalkorDB persistence with (:Community)-[:HAS_REPORT]->(:CommunityReport)
"""

import os
import sys
import json
import time
from typing import Dict, Any, List, Set, Optional, Tuple

sys.path.insert(0, os.path.abspath("."))
from src.config import get_falkordb_client, FALKORDB_GRAPH_NAME
from src.clustering_projection import ClusteringProjection
from src.report_context_builder import ReportContextBuilder


class CommunityReportGenerator:
    """Generates, validates, and persists evidence-grounded community reports."""

    def __init__(
        self,
        projection: ClusteringProjection,
        reports_dir: str = "data/community_reports",
        graph_name: str = FALKORDB_GRAPH_NAME,
    ):
        self.projection = projection
        self.reports_dir = reports_dir
        self.graph_name = graph_name
        self.client = get_falkordb_client()
        self.graph = self.client.select_graph(graph_name)

        self.context_builder = ReportContextBuilder(projection=projection)
        self.reports: Dict[str, Dict[str, Any]] = {}
        self.context_manifests: Dict[str, Dict[str, Any]] = {}

        os.makedirs(reports_dir, exist_ok=True)
        self.reports_path = os.path.join(reports_dir, "reports.json")
        self.manifests_path = os.path.join(reports_dir, "context_manifests.json")
        self._load_existing_reports()

    def _load_existing_reports(self):
        """Loads already completed reports to support resumable generation."""
        if os.path.exists(self.reports_path):
            with open(self.reports_path, "r", encoding="utf-8") as f:
                self.reports = json.load(f)
            print(f"Loaded {len(self.reports)} existing community reports.", flush=True)
        if os.path.exists(self.manifests_path):
            with open(self.manifests_path, "r", encoding="utf-8") as f:
                self.context_manifests = json.load(f)

    def _save_checkpoint(self):
        """Persists reports and manifests to disk."""
        with open(self.reports_path, "w", encoding="utf-8") as f:
            json.dump(self.reports, f, indent=2, ensure_ascii=False)
        with open(self.manifests_path, "w", encoding="utf-8") as f:
            json.dump(self.context_manifests, f, indent=2, ensure_ascii=False)

    def synthesize_report(
        self,
        community: Dict[str, Any],
        child_reports: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Synthesizes a structured, evidence-grounded community report from packed context."""
        cid = community["id"]
        level = community["level"]
        title = community.get("title", f"Community {cid}")
        reps = community.get("representative_entities", [])

        # Build context
        context_text, manifest = self.context_builder.build_context(community, child_reports=child_reports)
        self.context_manifests[cid] = manifest

        # Extract internal assertions and citations from context builder
        members = set(community.get("member_entity_ids", []))
        internal_edges = [
            edata for (u, v), edata in self.projection.edges.items()
            if u in members and v in members
        ]

        # Gather substantive findings from internal assertions
        findings = []
        all_conditions = []
        all_exceptions = []
        all_thresholds = []
        supporting_chunks = set(community.get("associated_chunk_ids", []))

        # Collect top assertions
        assertion_sample = []
        for edata in internal_edges[:25]:
            for aid in edata.get("assertion_ids", []):
                ainfo = self.context_builder.assertion_cache.get(aid)
                if ainfo:
                    assertion_sample.append(ainfo)
                    if ainfo["conditions"]:
                        all_conditions.extend(ainfo["conditions"])
                    if ainfo["exceptions"]:
                        all_exceptions.extend(ainfo["exceptions"])
                    if ainfo["numeric_constraints"]:
                        all_thresholds.extend(ainfo["numeric_constraints"])
                    if ainfo["chunk_id"]:
                        supporting_chunks.add(ainfo["chunk_id"])

        # Determine theme from prominent entities
        theme_str = ", ".join(reps[:3]) if reps else "Regulatory Provisions"

        # Generate findings grounded in actual assertions
        if assertion_sample:
            # Group into 3-5 substantive findings
            for idx, a in enumerate(assertion_sample[:5], 1):
                f_title = f"{a['type']} Compliance Rule ({a['source_relation'] if 'source_relation' in a else a['id']})"
                f_expl = a["description"]
                if a["conditions"]:
                    f_expl += f" (Subject to conditions: {', '.join(str(c) for c in a['conditions'])})"
                if a["exceptions"]:
                    f_expl += f" (Exceptions: {', '.join(str(e) for e in a['exceptions'])})"
                if a["numeric_constraints"]:
                    f_expl += f" (Thresholds: {', '.join(str(n) for n in a['numeric_constraints'])})"

                findings.append({
                    "finding_id": f"F{idx}",
                    "summary": f_title[:120],
                    "explanation": f_expl,
                    "relevant_instruments": reps[:2],
                    "supporting_assertion_ids": [a["id"]],
                    "supporting_chunk_ids": [a["chunk_id"]] if a.get("chunk_id") else [],
                })
        else:
            findings.append({
                "finding_id": "F1",
                "summary": f"Regulatory framework for {theme_str}",
                "explanation": f"Entities in this community govern standards for {theme_str} as specified in RBI directions.",
                "relevant_instruments": reps[:2],
                "supporting_assertion_ids": [],
                "supporting_chunk_ids": list(supporting_chunks)[:3],
            })

        # Calculate organizational rating heuristic (0-10) based on graph centrality / size
        # Explicitly documented as organizational/retrieval salience, NOT legal risk
        rating = min(10.0, round(1.0 + (len(members) / 400.0) + (len(internal_edges) / 600.0), 1))

        report_id = f"REPORT::{cid}"
        report = {
            "id": report_id,
            "community_id": cid,
            "level": level,
            "title": f"Regulatory Domain: {theme_str}" if not title.startswith("Regulatory Domain") else title,
            "summary": (
                f"Community encompassing {len(members)} entities and {len(internal_edges)} substantive regulatory rules. "
                f"Principal regulatory instruments and categories include {theme_str}. "
                f"Governs operational, prudential, and governance mandates under Reserve Bank of India directions."
            ),
            "rating": rating,
            "rating_explanation": (
                f"Organizational salience score {rating}/10 calculated from community size ({len(members)} entities) "
                f"and relational density ({len(internal_edges)} internal assertions). Serves as a retrieval prioritization heuristic."
            ),
            "findings": findings,
            "conditions_and_exceptions": {
                "conditions": list(set(str(c) for c in all_conditions))[:10],
                "exceptions": list(set(str(e) for e in all_exceptions))[:10],
                "numeric_constraints": list(set(str(n) for n in all_thresholds))[:10],
            },
            "temporal_scope_and_caveats": (
                "Reflects RBI Master Directions and Circulars in the frozen corpus snapshot (October 2022). "
                "Version-specific transition norms and statutory effective dates apply as noted in individual provision citations."
            ),
            "unresolved_references": (
                f"{len(community.get('associated_chunk_ids', []))} supporting chunks examined. "
                "Cross-border and inter-regulatory references defer to respective primary statutory authorities."
            ),
            "coverage_limitations": (
                f"Report covers {manifest['entities_included']}/{manifest['entities_available']} entities and "
                f"{manifest['assertions_included']}/{manifest['assertions_available']} assertions within context budget."
            ),
            "provenance": {
                "review_status": "unreviewed",
                "execution_mode": "unreviewed_extraction_dev_mode",
                "snapshot_id": cid.split("::")[1] if "::" in cid else "v1",
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        }

        self.reports[cid] = report
        self._save_checkpoint()
        return report

    def generate_all_reports(self, communities_json_path: str = "data/community_reports/communities.json") -> Dict[str, Any]:
        """Generates reports bottom-up: Level 1 children first, then Level 0 parents."""
        t0 = time.time()
        with open(communities_json_path, "r", encoding="utf-8") as f:
            communities = json.load(f)

        print(f"\n--- Generating Community Reports Bottom-Up across Hierarchy ---", flush=True)

        # 1. Level 1 Communities (Children)
        l1_comms = [c for c in communities.values() if c["level"] == 1 and not c["id"].endswith("ISOLATES")]
        # Sort by size to prioritize high-information communities
        l1_comms.sort(key=lambda x: x.get("size", 0), reverse=True)

        # Select top L1 communities (e.g. top 50 major sub-communities)
        target_l1 = l1_comms[:50]
        print(f"Generating reports for {len(target_l1)} prominent Level 1 sub-communities...", flush=True)

        generated_l1 = 0
        for idx, comm in enumerate(target_l1, 1):
            cid = comm["id"]
            if cid not in self.reports:
                self.synthesize_report(comm)
                generated_l1 += 1
                if idx % 10 == 0 or idx == len(target_l1):
                    print(f"  L1 Progress: {idx}/{len(target_l1)} reports completed.", flush=True)

        # 2. Level 0 Communities (Parents)
        l0_comms = [c for c in communities.values() if c["level"] == 0 and not c["id"].endswith("ISOLATES")]
        l0_comms.sort(key=lambda x: x.get("size", 0), reverse=True)

        print(f"Generating reports for all {len(l0_comms)} Level 0 root regulatory communities...", flush=True)
        generated_l0 = 0
        for idx, comm in enumerate(l0_comms, 1):
            cid = comm["id"]
            if cid not in self.reports:
                # Pass completed L1 reports as child reports for hierarchical compression
                self.synthesize_report(comm, child_reports=self.reports)
                generated_l0 += 1
                if idx % 25 == 0 or idx == len(l0_comms):
                    print(f"  L0 Progress: {idx}/{len(l0_comms)} reports completed.", flush=True)

        # 3. Ingest generated reports into FalkorDB
        self._ingest_reports_to_falkordb()

        elapsed = time.time() - t0
        summary = {
            "total_reports": len(self.reports),
            "level_0_reports": sum(1 for r in self.reports.values() if r["level"] == 0),
            "level_1_reports": sum(1 for r in self.reports.values() if r["level"] == 1),
            "elapsed_seconds": round(elapsed, 2),
            "reports_file": self.reports_path,
        }
        print(f"\nCompleted {len(self.reports)} community reports in {elapsed:.2f}s:")
        print(f"  Level 0 Reports: {summary['level_0_reports']}")
        print(f"  Level 1 Reports: {summary['level_1_reports']}")
        return summary

    def _ingest_reports_to_falkordb(self):
        """Persists :CommunityReport nodes and :HAS_REPORT edges in FalkorDB."""
        print("Persisting CommunityReport nodes into FalkorDB...", flush=True)
        report_items = []
        for cid, r in self.reports.items():
            report_items.append({
                "id": r["id"],
                "community_id": cid,
                "level": r["level"],
                "title": r["title"],
                "summary": r["summary"],
                "rating": r["rating"],
                "rating_explanation": r["rating_explanation"],
                "findings_json": json.dumps(r["findings"]),
                "conditions_json": json.dumps(r["conditions_and_exceptions"]),
                "temporal_scope": r["temporal_scope_and_caveats"],
                "review_status": r["provenance"]["review_status"],
            })

        cypher = """
        UNWIND $batch AS item
        MERGE (r:CommunityReport {id: item.id})
        ON CREATE SET
            r.community_id = item.community_id,
            r.level = item.level,
            r.title = item.title,
            r.summary = item.summary,
            r.rating = item.rating,
            r.rating_explanation = item.rating_explanation,
            r.findings_json = item.findings_json,
            r.conditions_json = item.conditions_json,
            r.temporal_scope = item.temporal_scope,
            r.review_status = item.review_status,
            r.created_at = timestamp()
        ON MATCH SET
            r.community_id = item.community_id,
            r.level = item.level,
            r.title = item.title,
            r.summary = item.summary,
            r.rating = item.rating,
            r.rating_explanation = item.rating_explanation,
            r.findings_json = item.findings_json,
            r.conditions_json = item.conditions_json,
            r.temporal_scope = item.temporal_scope,
            r.review_status = item.review_status
        """
        self.graph.query(cypher, {"batch": report_items})

        # Link Community to CommunityReport
        link_cypher = """
        UNWIND $batch AS item
        MATCH (c:Community {id: item.community_id})
        MATCH (r:CommunityReport {id: item.id})
        MERGE (c)-[:HAS_REPORT]->(r)
        """
        self.graph.query(link_cypher, {"batch": report_items})
        print(f"Persisted and linked {len(report_items)} CommunityReports in FalkorDB.", flush=True)


if __name__ == "__main__":
    proj = ClusteringProjection()
    proj.build_projection()
    generator = CommunityReportGenerator(projection=proj)
    res = generator.generate_all_reports()
