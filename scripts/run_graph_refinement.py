"""Graph Refinement & Verification Engine using Multi-Node Cluster LLMs.
Loads an evaluation batch, sends each chunk along with its current triplets to
the local LLM cluster, audits existing triplets, and extracts missing legal triplets.

Usage:
    # Run evaluation on a packaged batch using active cluster nodes
    python scripts/run_graph_refinement.py --batch data/eval_batches/eval_batch_RBI_...json

    # Test run locally in dry-run mode (validates schema & pipeline without calling LLM)
    python scripts/run_graph_refinement.py --batch data/eval_batches/eval_batch_RBI_...json --dry-run
"""

import os
import sys
import json
import time
import asyncio
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional

# Add root directory to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from cluster.cluster_dispatcher import MultiNodeLLMDispatcher


SYSTEM_PROMPT = """You are an expert Indian Regulatory Legal Knowledge Graph Auditor.
Your task is to analyze the provided Verbatim Legal Text against its Currently Extracted Knowledge Graph Triplets.

You must perform two tasks:
1. AUDIT EXISTING TRIPLETS:
   - For each extracted triplet, verify whether it is accurately grounded in the text.
   - Mark is_valid: true if accurate, false if hallucinated or distorted, with a concise critique.

2. DISCOVER MISSING TRIPLETS:
   - Identify regulatory obligations, prohibitions, permissions, defined terms, exceptions, or thresholds in the text that were MISSED by the current extraction.
   - For every missing relation, provide:
     * subject (canonical entity name) and subject_type (Authority, Instrument, Provision, Organization, Role, Requirement, DefinedTerm)
     * predicate (MUST be one of: REQUIRES, PROHIBITS, PERMITS, APPLIES_TO, DEFINES, HAS_PROVISION, REFERS_TO, EXEMPTS_FROM, AMENDS, SUPERSEDES)
     * object and object_type
     * exact_quote: verbatim text from the passage proving this relationship
     * conditions: any logical conditions (e.g. "if turnover exceeds...")
     * exceptions: any explicit statutory exceptions (e.g. "except for small finance banks")
     * numeric_constraints: numeric thresholds or time limits (e.g. "within 30 days", "15% CRAR")

OUTPUT FORMAT:
Return ONLY valid JSON matching this exact structure:
{
  "audited_triplets": [
    {
      "relationship_id": "R1",
      "is_valid": true,
      "critique": "Accurately represents Section 4 mandate."
    }
  ],
  "missing_triplets": [
    {
      "subject": "Commercial Bank",
      "subject_type": "Organization",
      "predicate": "REQUIRES",
      "object": "Submit Quarterly Capital Return",
      "object_type": "Requirement",
      "description": "Commercial Banks must submit quarterly capital adequacy returns to RBI.",
      "exact_quote": "Every commercial bank shall submit a quarterly return on capital adequacy...",
      "conditions": [],
      "exceptions": ["Regional Rural Banks are exempt"],
      "numeric_constraints": ["quarterly", "within 21 days from quarter end"]
    }
  ]
}
"""


def build_chunk_prompt(chunk: Dict[str, Any], existing_triplets: List[Dict[str, Any]]) -> str:
    triplet_lines = []
    for idx, t in enumerate(existing_triplets, 1):
        subj = t.get("subject", "Unknown")
        s_type = t.get("subject_type", "Entity")
        pred = t.get("predicate", "RELATION")
        obj = t.get("object", "Unknown")
        o_type = t.get("object_type", "Entity")
        desc = t.get("description", "")
        conds = t.get("conditions_json") or []
        excs = t.get("exceptions_json") or []

        q_info = []
        if conds:
            q_info.append(f"Conditions: {conds}")
        if excs:
            q_info.append(f"Exceptions: {excs}")
        q_str = f" ({'; '.join(q_info)})" if q_info else ""

        triplet_lines.append(f"[{t.get('local_relationship_id') or f'R{idx}'}] ({subj}:{s_type}) --[{pred}]--> ({obj}:{o_type}) | {desc}{q_str}")

    triplets_block = "\n".join(triplet_lines) if triplet_lines else "None (0 existing triplets extracted for this chunk)"

    prompt = f"""=== VERBATIM REGULATORY TEXT (Chunk ID: {chunk['chunk_id']}) ===
{chunk.get('raw_text', '').strip()}

=== CURRENTLY EXTRACTED TRIPLETS ({len(existing_triplets)}) ===
{triplets_block}

Analyze the verbatim text, audit the existing triplets, and extract any missing statutory triplets. Output ONLY valid JSON."""
    return prompt


async def evaluate_batch(
    batch_path: Path,
    dispatcher: MultiNodeLLMDispatcher,
    max_chunks: Optional[int] = None,
    dry_run: bool = False
) -> Dict[str, Any]:
    with open(batch_path, "r", encoding="utf-8") as f:
        batch_data = json.load(f)

    chunks = batch_data["chunks"]
    if max_chunks:
        chunks = chunks[:max_chunks]

    triplets_by_chunk = batch_data.get("triplets_by_chunk", {})
    print(f"\nEvaluating batch: {batch_data['batch_id']}")
    print(f"Document: {batch_data['document_title']}")
    print(f"Total chunks to evaluate: {len(chunks)}")

    results = []
    total_audited = 0
    total_valid = 0
    total_invalid = 0
    total_suggested_missing = 0

    t_start = time.time()

    for idx, chunk in enumerate(chunks, 1):
        cid = chunk["chunk_id"]
        existing = triplets_by_chunk.get(cid, [])
        prompt = build_chunk_prompt(chunk, existing)

        if dry_run:
            # Mock evaluation for dry run testing
            mock_res = {
                "audited_triplets": [{"relationship_id": t.get("local_relationship_id", f"R{i}"), "is_valid": True, "critique": "Dry-run verified."} for i, t in enumerate(existing, 1)],
                "missing_triplets": []
            }
            results.append({"chunk_id": cid, "evaluation": mock_res, "status": "dry_run"})
            total_audited += len(existing)
            total_valid += len(existing)
            continue

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]

        print(f"[{idx}/{len(chunks)}] Dispatching chunk {cid} ({len(existing)} existing triplets)...", end="", flush=True)
        t0 = time.time()
        try:
            resp = await dispatcher.chat_completion(
                messages=messages,
                temperature=0.0,
                max_tokens=2048,
                response_format={"type": "json_object"}
            )
            elapsed = time.time() - t0
            content = resp["choices"][0]["message"]["content"]
            parsed = json.loads(content)

            audited = parsed.get("audited_triplets", [])
            missing = parsed.get("missing_triplets", [])

            total_audited += len(audited)
            total_valid += sum(1 for a in audited if a.get("is_valid"))
            total_invalid += sum(1 for a in audited if not a.get("is_valid"))
            total_suggested_missing += len(missing)

            results.append({
                "chunk_id": cid,
                "elapsed_sec": round(elapsed, 2),
                "evaluation": parsed,
                "status": "success"
            })
            print(f" Done ({elapsed:.2f}s). Valid: {len(audited)} | Missing Suggested: {len(missing)}")

        except Exception as ex:
            print(f" FAILED: {ex}")
            results.append({
                "chunk_id": cid,
                "error": str(ex),
                "status": "failed"
            })

    total_time = time.time() - t_start

    summary = {
        "batch_id": batch_data["batch_id"],
        "document_title": batch_data["document_title"],
        "total_chunks_evaluated": len(chunks),
        "total_audited_triplets": total_audited,
        "valid_triplets": total_valid,
        "invalid_triplets": total_invalid,
        "new_missing_triplets_suggested": total_suggested_missing,
        "total_wall_time_sec": round(total_time, 2),
        "results": results
    }

    # Save results
    out_dir = batch_path.parent / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"refinement_results_{batch_data['batch_id']}.json"

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 80)
    print(" " * 28 + "EVALUATION SUMMARY")
    print("=" * 80)
    print(f"Chunks Evaluated           : {len(chunks)}")
    print(f"Existing Triplets Audited  : {total_audited}")
    print(f"Valid Existing Triplets    : {total_valid}")
    print(f"Hallucinated / Invalid     : {total_invalid}")
    print(f"New Triplets Suggested     : {total_suggested_missing}")
    print(f"Total Wall Time            : {total_time:.2f} s")
    print(f"Saved Results to           : {out_file}")
    print("=" * 80 + "\n")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Run LLM Knowledge Graph Refinement & Evaluation.")
    parser.add_argument("--batch", type=str, required=True, help="Path to packaged batch JSON file.")
    parser.add_argument("--max-chunks", type=int, default=None, help="Limit number of chunks to evaluate.")
    parser.add_argument("--registry-dir", type=str, default=None, help="Path to cluster node registry directory.")
    parser.add_argument("--urls", type=str, default="", help="Comma-separated static node URLs.")
    parser.add_argument("--dry-run", action="store_true", help="Run local mock dry-run without calling LLMs.")

    args = parser.parse_args()
    batch_path = Path(args.batch)
    if not batch_path.exists():
        print(f"Error: Batch file not found at {batch_path}")
        return

    explicit_urls = [u.strip() for u in args.urls.split(",") if u.strip()] if args.urls else None
    dispatcher = MultiNodeLLMDispatcher(registry_dir=args.registry_dir, explicit_urls=explicit_urls)

    asyncio.run(evaluate_batch(batch_path, dispatcher, max_chunks=args.max_chunks, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
