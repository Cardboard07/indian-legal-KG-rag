"""Pass 2 Context-Enriched Refinement Engine.
Selectively re-evaluates triplets marked as 'needs_context' (is_valid: null)
by supplying expanded context: Document Preamble + Sliding Window (Chunks N-1, N, N+1).

Usage:
    # 1. Preview unresolved triplets across all results
    python scripts/refine_context_pass.py --preview

    # 2. Run Pass 2 dry-run test
    python scripts/refine_context_pass.py --dry-run

    # 3. Run Pass 2 live against Turing cluster
    python scripts/refine_context_pass.py --concurrency 8
"""

import os
import sys
import json
import time
import sqlite3
import asyncio
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src.config import DB_PATH, ENTITY_TYPES, PERMITTED_RELATIONSHIP_ENDPOINTS
from cluster.cluster_dispatcher import MultiNodeLLMDispatcher
from src.refinement_contract import (
    normalize_proposal_qualifiers,
    validate_proposal,
)


PASS2_SYSTEM_PROMPT = """You resolve context-dependent regulatory relationships against expanded document context.
The target relationship was previously marked 'needs_context' because an isolated chunk lacked
surrounding context (e.g. document header, issuing authority, or cross-referenced section).

You are now provided with:
1. Document Preamble (issuing authority, instrument title, date, and overall scope).
2. Preceding section context (Chunk N-1).
3. Target section text (Chunk N).
4. Succeeding section context (Chunk N+1).

TASK:
Re-evaluate EVERY supplied relationship:
1. is_valid is:
   - true: if supported by the combined document context (preamble + surrounding sections).
   - false: if factually contradicted, distorted, or completely unsupported.
2. For false: provide error_type (hallucination, inversion_or_lost_exception, wrong_predicate, unsupported_scope).
   If the relationship represents a genuine rule in the text, provide the corrected_triplet.
   If pure hallucination, set corrected_triplet to null.
3. For true: set error_type to "none" and corrected_triplet to null.
4. Explain your factual basis in critique.

OUTPUT FORMAT:
Return ONLY a valid JSON object matching:
{
  "resolved_triplets": [
    {
      "relationship_id": "string",
      "is_valid": true,
      "critique": "Supported by the document preamble confirming RBI issued this circular.",
      "error_type": "none",
      "corrected_triplet": null
    }
  ]
}
"""


def extract_json_response(content: str) -> Dict[str, Any]:
    clean = content.strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    elif clean.startswith("```"):
        clean = clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    clean = clean.strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        start = clean.find("{")
        end = clean.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(clean[start : end + 1])
        raise


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_chunk_text(file_path_str: str) -> str:
    fpath = Path(file_path_str)
    if not fpath.is_absolute():
        fpath = ROOT_DIR / fpath
    if not fpath.is_file():
        # Fallback to hybrid chunks directory
        fpath = ROOT_DIR / "hpc_results" / "chunks_docling_hybrid" / Path(file_path_str).name
    if fpath.is_file():
        try:
            return fpath.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return ""


def build_expanded_context(
    conn: sqlite3.Connection,
    chunk_id: str
) -> Tuple[str, str]:
    """Retrieves document preamble and adjacent chunks (N-1, N, N+1)."""
    c_row = conn.execute(
        "SELECT chunk_id, document_id, chunk_index, file_path, section_locator FROM manifest_chunks WHERE chunk_id = ?",
        (chunk_id,)
    ).fetchone()
    if not c_row:
        return "", ""

    doc_id = c_row["document_id"]
    c_idx = c_row["chunk_index"]

    # 1. Fetch document metadata
    doc_row = conn.execute("SELECT * FROM documents WHERE document_id = ?", (doc_id,)).fetchone()
    doc_title = doc_row["document_title"] if doc_row else doc_id
    regulator = doc_row["regulator"] if doc_row else "Regulatory Authority"

    # 2. Fetch Preamble (Chunk 0 or 1)
    preamble_row = conn.execute(
        "SELECT file_path FROM manifest_chunks WHERE document_id = ? ORDER BY chunk_index ASC LIMIT 1",
        (doc_id,)
    ).fetchone()
    preamble_text = load_chunk_text(preamble_row["file_path"]) if preamble_row else ""

    # 3. Fetch Adjacent chunks (N-1, N, N+1)
    adj_rows = conn.execute(
        """
        SELECT chunk_index, file_path, section_locator
        FROM manifest_chunks
        WHERE document_id = ? AND chunk_index BETWEEN ? AND ?
        ORDER BY chunk_index ASC
        """,
        (doc_id, max(0, c_idx - 1), c_idx + 1)
    ).fetchall()

    target_text = ""
    prev_text = ""
    next_text = ""

    for r in adj_rows:
        text = load_chunk_text(r["file_path"])
        if r["chunk_index"] == c_idx:
            target_text = text
        elif r["chunk_index"] < c_idx:
            prev_text = text
        elif r["chunk_index"] > c_idx:
            next_text = text

    # Formulate assembled prompt context
    context_blocks = [
        f"=== DOCUMENT HEADER & PREAMBLE ===",
        f"Document: {doc_title} | Regulator: {regulator}",
        f"Preamble Excerpt:\n{preamble_text[:1500]}\n"
    ]

    if prev_text:
        context_blocks.append(f"=== PRECEDING SECTION (Chunk index {c_idx - 1}) ===\n{prev_text[:1200]}\n")

    context_blocks.append(f"=== TARGET STATUTORY SECTION (Chunk index {c_idx}) ===\n{target_text}\n")

    if next_text:
        context_blocks.append(f"=== SUCCEEDING SECTION (Chunk index {c_idx + 1}) ===\n{next_text[:1200]}\n")

    combined_context = "\n".join(context_blocks)
    full_text_for_quote_check = f"{preamble_text}\n{prev_text}\n{target_text}\n{next_text}"

    return combined_context, full_text_for_quote_check


async def run_context_pass(
    results_dir: Path,
    concurrency: int = 8,
    dry_run: bool = False,
    dispatcher: Optional[MultiNodeLLMDispatcher] = None,
):
    result_files = sorted(list(results_dir.glob("refinement_results_*.json")))
    if not result_files:
        print(f"No refinement result files found in {results_dir}")
        return

    conn = get_db()

    total_unresolved = 0
    chunks_with_unresolved = []

    for rf in result_files:
        try:
            data = json.loads(rf.read_text(encoding="utf-8"))
        except Exception:
            continue

        for res in data.get("results", []):
            cid = res.get("chunk_id")
            eval_data = res.get("evaluation", {})
            unresolved = [
                a for a in eval_data.get("audited_triplets", [])
                if a.get("is_valid") is None
            ]
            if unresolved:
                total_unresolved += len(unresolved)
                chunks_with_unresolved.append({
                    "result_file": rf,
                    "chunk_id": cid,
                    "unresolved_triplets": unresolved,
                })

    print("\n" + "=" * 80)
    print(" " * 24 + "PASS 2: CONTEXT-ENRICHED REFINEMENT")
    print("=" * 80)
    print(f"Results Directory               : {results_dir}")
    print(f"Result Files Inspected          : {len(result_files)}")
    print(f"Chunks Requiring Expanded Context: {len(chunks_with_unresolved)}")
    print(f"Total Triplets to Re-evaluate   : {total_unresolved}")
    print(f"Execution Mode                  : {'DRY RUN' if dry_run else 'LIVE CLUSTER INFERENCE'}")
    print("=" * 80 + "\n")

    if total_unresolved == 0:
        print("All triplets are already conclusively audited (0 'needs_context' remaining).")
        conn.close()
        return

    if not dry_run and dispatcher:
        healthy = await dispatcher.update_health()
        if not healthy:
            print("ERROR: No healthy LLM worker nodes online. Aborting Pass 2.")
            conn.close()
            return

    sem = asyncio.Semaphore(concurrency)
    resolved_count = 0
    t0 = time.time()

    async def process_chunk_unresolved(item: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal resolved_count
        cid = item["chunk_id"]
        triplets = item["unresolved_triplets"]
        expanded_context, full_source_text = build_expanded_context(conn, cid)

        payload = {
            "chunk_id": cid,
            "expanded_context": expanded_context,
            "target_triplets": triplets,
            "schema": {"entity_types": ENTITY_TYPES, "endpoint_rules": {
                k: {side: sorted(types) for side, types in rule.items()}
                for k, rule in PERMITTED_RELATIONSHIP_ENDPOINTS.items()
            }}
        }

        user_prompt = json.dumps(payload, ensure_ascii=False)

        async with sem:
            if dry_run:
                # Mock resolution: resolve document-level authority relations as valid
                resolved = []
                for t in triplets:
                    resolved.append({
                        "relationship_id": t["relationship_id"],
                        "is_valid": True,
                        "critique": "Verified against document preamble in expanded context (dry-run).",
                        "error_type": "none",
                        "corrected_triplet": None
                    })
                eval_out = {"resolved_triplets": resolved}
            else:
                messages = [
                    {"role": "system", "content": PASS2_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ]
                resp = await dispatcher.chat_completion(
                    messages=messages,
                    temperature=0.0,
                    max_tokens=2048,
                    response_format={"type": "json_object"}
                )
                content = resp["choices"][0]["message"]["content"]
                eval_out = extract_json_response(content)

            # Validate resolutions
            resolutions_map = {}
            for r in eval_out.get("resolved_triplets", []):
                rid = r.get("relationship_id")
                v = r.get("is_valid")
                if rid and (v is True or v is False):
                    resolutions_map[rid] = r

            resolved_count += len(triplets)
            print(f"Resolved [{resolved_count}/{total_unresolved}] Chunk {cid[:28]} | Resolved: {len(resolutions_map)}/{len(triplets)}")
            return {
                "chunk_id": cid,
                "result_file": item["result_file"],
                "resolutions": resolutions_map
            }

    tasks = [process_chunk_unresolved(item) for item in chunks_with_unresolved]
    chunk_resolutions = await asyncio.gather(*tasks)

    # In-place update of result files
    file_updates: Dict[Path, Dict[str, Dict[str, Any]]] = {}
    for cr in chunk_resolutions:
        rf = cr["result_file"]
        file_updates.setdefault(rf, {})[cr["chunk_id"]] = cr["resolutions"]

    updated_files_count = 0
    final_valid_added = 0
    final_invalid_added = 0
    final_repaired_added = 0

    for rf, chunk_map in file_updates.items():
        try:
            data = json.loads(rf.read_text(encoding="utf-8"))
        except Exception:
            continue

        file_changed = False
        for res in data.get("results", []):
            cid = res.get("chunk_id")
            if cid in chunk_map:
                res_map = chunk_map[cid]
                eval_data = res.get("evaluation", {})
                audited_list = eval_data.get("audited_triplets", [])
                for a in audited_list:
                    rid = a.get("relationship_id")
                    if a.get("is_valid") is None and rid in res_map:
                        res_item = res_map[rid]
                        a["is_valid"] = res_item.get("is_valid")
                        a["critique"] = res_item.get("critique", a.get("critique"))
                        a["error_type"] = res_item.get("error_type", "none")
                        if res_item.get("corrected_triplet"):
                            a["corrected_triplet"] = res_item.get("corrected_triplet")
                            final_repaired_added += 1
                        if a["is_valid"] is True:
                            final_valid_added += 1
                        elif a["is_valid"] is False:
                            final_invalid_added += 1
                        file_changed = True

        if file_changed:
            # Recompile file summary
            total_aud = 0
            v_cnt = 0
            inv_cnt = 0
            rep_cnt = 0
            nc_cnt = 0
            for res in data.get("results", []):
                for a in res.get("evaluation", {}).get("audited_triplets", []):
                    total_aud += 1
                    if a.get("is_valid") is True:
                        v_cnt += 1
                    elif a.get("is_valid") is False:
                        inv_cnt += 1
                        if a.get("corrected_triplet"):
                            rep_cnt += 1
                    elif a.get("is_valid") is None:
                        nc_cnt += 1
            data["valid_triplets"] = v_cnt
            data["invalid_triplets"] = inv_cnt
            data["repaired_triplets"] = rep_cnt
            data["needs_context_triplets"] = nc_cnt

            with open(rf, "w", encoding="utf-8") as fp:
                json.dump(data, fp, indent=2, ensure_ascii=False)
            updated_files_count += 1

    elapsed = time.time() - t0
    conn.close()

    print("\n" + "=" * 80)
    print(" " * 25 + "PASS 2 REFINEMENT COMPLETE")
    print("=" * 80)
    print(f"Total Unresolved Re-evaluated   : {total_unresolved}")
    print(f"  - Newly Validated (Grounded)  : {final_valid_added}")
    print(f"  - Newly Invalidated           : {final_invalid_added}")
    print(f"    * Repaired with Correction  : {final_repaired_added}")
    print(f"Result Files Updated            : {updated_files_count}")
    print(f"Total Wall Time                 : {elapsed:.2f} s")
    print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Run Pass 2 Context-Enriched Refinement for unresolved triplets.")
    parser.add_argument("--results-dir", type=str, default="data/eval_batches/results", help="Directory containing Pass 1 results.")
    parser.add_argument("--concurrency", type=int, default=8, help="Concurrency for live inference.")
    parser.add_argument("--registry-dir", type=str, default=None, help="Cluster registry directory.")
    parser.add_argument("--urls", type=str, default="", help="Comma-separated explicit URLs.")
    parser.add_argument("--dry-run", action="store_true", help="Run local dry-run simulation.")
    parser.add_argument("--preview", action="store_true", help="Preview unresolved triplets without executing.")

    args = parser.parse_args()
    results_dir = Path(args.results_dir)

    if args.preview:
        conn = get_db()
        result_files = sorted(list(results_dir.glob("refinement_results_*.json")))
        total_nc = 0
        chunks_map = {}
        for rf in result_files:
            data = json.loads(rf.read_text(encoding="utf-8"))
            for res in data.get("results", []):
                unresolved = [a for a in res.get("evaluation", {}).get("audited_triplets", []) if a.get("is_valid") is None]
                if unresolved:
                    total_nc += len(unresolved)
                    chunks_map[res["chunk_id"]] = len(unresolved)
        print("\n" + "=" * 80)
        print("PREVIEW: UNRESOLVED TRIPLETS (NEEDS_CONTEXT)")
        print("=" * 80)
        print(f"Total Unresolved Triplets       : {total_nc}")
        print(f"Total Chunks Needing Wide Context: {len(chunks_map)}")
        print(f"Average Triplets per Chunk      : {total_nc / max(len(chunks_map), 1):.1f}")
        print("=" * 80 + "\n")
        conn.close()
        return

    explicit_urls = [u.strip() for u in args.urls.split(",") if u.strip()] if args.urls else None
    dispatcher = MultiNodeLLMDispatcher(registry_dir=args.registry_dir, explicit_urls=explicit_urls) if not args.dry_run else None

    asyncio.run(run_context_pass(
        results_dir=results_dir,
        concurrency=args.concurrency,
        dry_run=args.dry_run,
        dispatcher=dispatcher
    ))


if __name__ == "__main__":
    main()
