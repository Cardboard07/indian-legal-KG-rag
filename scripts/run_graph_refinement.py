"""Graph Refinement & Verification Engine using Multi-Node Cluster LLMs.
Loads an evaluation batch, sends each chunk along with its current triplets to
the local LLM cluster concurrently, audits existing triplets, and extracts missing legal triplets.

Usage:
    # Run evaluation on a packaged batch using active cluster nodes with concurrency 8
    python scripts/run_graph_refinement.py --batch data/eval_batches/eval_batch_RBI_...json --concurrency 8

    # Test run locally in dry-run mode (validates schema & pipeline without calling LLM)
    python scripts/run_graph_refinement.py --batch data/eval_batches/eval_batch_RBI_...json --dry-run
"""

import os
import sys
import json
import time
import re
import asyncio
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional, Set

# Add root directory to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from cluster.cluster_dispatcher import MultiNodeLLMDispatcher


SYSTEM_PROMPT = """You are an expert Indian Regulatory Legal Knowledge Graph Auditor.
Your task is to analyze the provided Verbatim Legal Text against its Currently Extracted Knowledge Graph Triplets.

You must perform two tasks:
1. AUDIT EXISTING TRIPLETS:
   - For each extracted triplet, verify whether it is accurately grounded in the text.
   - Mark is_valid: true if accurate, false if hallucinated, inverted, or distorted, with a concise critique.
   - Include error_type: one of ["none", "hallucination", "inversion_or_lost_exception", "wrong_predicate", "unsupported_scope"]

2. DISCOVER MISSING TRIPLETS:
   - Identify regulatory obligations, prohibitions, permissions, defined terms, exceptions, or thresholds in the text that were MISSED by the current extraction.
   - For every missing relation, provide:
     * subject (canonical entity name) and subject_type (Authority, Instrument, Provision, Organization, Role, Requirement, DefinedTerm)
     * predicate (MUST be one of: REQUIRES, PROHIBITS, PERMITS, APPLIES_TO, DEFINES, HAS_PROVISION, REFERS_TO, EXEMPTS_FROM, AMENDS, SUPERSEDES)
     * object and object_type
     * description: clear, qualified summary of the statutory relation
     * exact_quote: verbatim text from the passage proving this relationship
     * conditions: list of logical conditions (e.g. ["if asset turnover exceeds 10 crore"])
     * exceptions: list of explicit statutory exceptions (e.g. ["Regional Rural Banks are exempt"])
     * numeric_constraints: list of numeric thresholds (e.g. [{"metric": "CRAR", "operator": ">=", "value": 15, "unit": "percent"}])
     * temporal_qualifiers: list of temporal constraints (e.g. [{"qualifier_type": "deadline", "value_text": "within 30 days"}])

OUTPUT FORMAT:
Return ONLY valid JSON matching this exact structure:
{
  "audited_triplets": [
    {
      "relationship_id": "R1",
      "is_valid": true,
      "critique": "Accurately represents Section 4 mandate.",
      "error_type": "none"
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
      "numeric_constraints": [
        {"metric": "reporting frequency", "operator": "==", "value": "quarterly", "unit": "frequency"}
      ],
      "temporal_qualifiers": [
        {"qualifier_type": "deadline", "value_text": "within 21 days from quarter end"}
      ]
    }
  ]
}
"""


def extract_json_response(content: str) -> Dict[str, Any]:
    """Robustly extracts JSON dictionary from model output, handling markdown code fences."""
    clean = content.strip()
    # Strip markdown fences if present
    if "```json" in clean:
        match = re.search(r"```json\s*(\{.*?\})\s*```", clean, re.DOTALL)
        if match:
            clean = match.group(1).strip()
    elif "```" in clean:
        match = re.search(r"```\s*(\{.*?\})\s*```", clean, re.DOTALL)
        if match:
            clean = match.group(1).strip()

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        # Fallback: find outermost curly braces
        start = clean.find("{")
        end = clean.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(clean[start : end + 1])
        raise


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
    concurrency: int = 8,
    dry_run: bool = False
) -> Dict[str, Any]:
    with open(batch_path, "r", encoding="utf-8") as f:
        batch_data = json.load(f)

    chunks = batch_data["chunks"]
    if max_chunks:
        chunks = chunks[:max_chunks]

    triplets_by_chunk = batch_data.get("triplets_by_chunk", {})
    batch_id = batch_data["batch_id"]

    print(f"\n" + "=" * 80)
    print(f"EVALUATING BATCH: {batch_id}")
    print(f"Document        : {batch_data.get('document_title', 'Unknown')}")
    print(f"Total Chunks    : {len(chunks)}")
    print(f"Concurrency     : {concurrency} parallel requests")
    print(f"Mode            : {'DRY RUN (Mock Inference)' if dry_run else 'LIVE CLUSTER INFERENCE'}")
    print("=" * 80 + "\n")

    # Set up checkpoints directory & file
    checkpoints_dir = batch_path.parent / "checkpoints"
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_file = checkpoints_dir / f"checkpoint_{batch_id}.jsonl"

    # Load existing checkpoint items to support idempotent resumption
    completed_chunks: Dict[str, Dict[str, Any]] = {}
    if checkpoint_file.exists():
        with open(checkpoint_file, "r", encoding="utf-8") as fp:
            for line in fp:
                line_str = line.strip()
                if line_str:
                    try:
                        record = json.loads(line_str)
                        if record.get("status") in ("success", "dry_run"):
                            completed_chunks[record["chunk_id"]] = record
                    except Exception:
                        pass

    if completed_chunks:
        print(f"Found {len(completed_chunks)} already completed chunks in checkpoint ({checkpoint_file.name}).")

    pending_chunks = [c for c in chunks if c["chunk_id"] not in completed_chunks]
    print(f"Pending Chunks to process: {len(pending_chunks)} / {len(chunks)}\n")

    # Lock for thread-safe checkpoint appends
    file_lock = asyncio.Lock()
    sem = asyncio.Semaphore(concurrency)

    completed_count = len(completed_chunks)
    total_chunks_count = len(chunks)
    t_start = time.time()

    async def process_single_chunk(chunk: Dict[str, Any], idx: int) -> Dict[str, Any]:
        nonlocal completed_count
        cid = chunk["chunk_id"]
        existing = triplets_by_chunk.get(cid, [])
        prompt = build_chunk_prompt(chunk, existing)

        async with sem:
            if dry_run:
                # Deterministic mock response for dry-run validation
                audited = []
                for i, t in enumerate(existing, 1):
                    rid = t.get("local_relationship_id") or f"R{i}"
                    audited.append({
                        "relationship_id": rid,
                        "is_valid": True,
                        "critique": f"Dry-run verified against verbatim chunk {cid}.",
                        "error_type": "none"
                    })
                mock_eval = {
                    "audited_triplets": audited,
                    "missing_triplets": [
                        {
                            "subject": "Regulated Entity",
                            "subject_type": "Organization",
                            "predicate": "REQUIRES",
                            "object": "Compliance Audit",
                            "object_type": "Requirement",
                            "description": "Entity must conduct an annual compliance audit.",
                            "exact_quote": chunk.get("raw_text", "")[:80].strip(),
                            "conditions": [],
                            "exceptions": [],
                            "numeric_constraints": [],
                            "temporal_qualifiers": []
                        }
                    ] if len(existing) == 0 and len(chunk.get("raw_text", "")) > 50 else []
                }
                res = {
                    "chunk_id": cid,
                    "elapsed_sec": 0.01,
                    "evaluation": mock_eval,
                    "status": "dry_run"
                }
            else:
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ]
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
                    parsed = extract_json_response(content)

                    # Ensure standard keys exist
                    parsed.setdefault("audited_triplets", [])
                    parsed.setdefault("missing_triplets", [])

                    res = {
                        "chunk_id": cid,
                        "elapsed_sec": round(elapsed, 2),
                        "evaluation": parsed,
                        "status": "success"
                    }
                except Exception as ex:
                    res = {
                        "chunk_id": cid,
                        "elapsed_sec": 0.0,
                        "error": str(ex),
                        "status": "failed"
                    }

            # Atomically append to checkpoint file
            async with file_lock:
                completed_count += 1
                with open(checkpoint_file, "a", encoding="utf-8") as fp:
                    fp.write(json.dumps(res, ensure_ascii=False) + "\n")
                    fp.flush()

                eval_data = res.get("evaluation", {})
                n_audited = len(eval_data.get("audited_triplets", []))
                n_missing = len(eval_data.get("missing_triplets", []))
                status_str = "OK" if res["status"] in ("success", "dry_run") else "ERR"

                print(
                    f"[{completed_count:3d}/{total_chunks_count:3d}] Chunk {cid[:28]} | "
                    f"Status: {status_str} | Audited: {n_audited} | Missing: {n_missing} | "
                    f"{res.get('elapsed_sec', 0.0):.2f}s",
                    flush=True
                )

            return res

    # Run pending chunks concurrently
    if pending_chunks:
        tasks = [process_single_chunk(c, i) for i, c in enumerate(pending_chunks, 1)]
        await asyncio.gather(*tasks, return_exceptions=True)

    # Read back complete consolidated results from checkpoint file
    all_records: Dict[str, Dict[str, Any]] = {}
    if checkpoint_file.exists():
        with open(checkpoint_file, "r", encoding="utf-8") as fp:
            for line in fp:
                line_str = line.strip()
                if line_str:
                    try:
                        record = json.loads(line_str)
                        all_records[record["chunk_id"]] = record
                    except Exception:
                        pass

    # Compile final metrics across the entire batch
    total_audited = 0
    total_valid = 0
    total_invalid = 0
    total_missing = 0
    final_results = []

    for c in chunks:
        cid = c["chunk_id"]
        rec = all_records.get(cid, {"chunk_id": cid, "status": "unprocessed"})
        final_results.append(rec)

        eval_data = rec.get("evaluation", {})
        for a in eval_data.get("audited_triplets", []):
            total_audited += 1
            if a.get("is_valid"):
                total_valid += 1
            else:
                total_invalid += 1
        total_missing += len(eval_data.get("missing_triplets", []))

    total_time = time.time() - t_start

    summary = {
        "batch_id": batch_id,
        "document_title": batch_data.get("document_title", "Unknown"),
        "total_chunks_evaluated": len(chunks),
        "total_audited_triplets": total_audited,
        "valid_triplets": total_valid,
        "invalid_triplets": total_invalid,
        "new_missing_triplets_suggested": total_missing,
        "total_wall_time_sec": round(total_time, 2),
        "results": final_results
    }

    # Save finalized results JSON
    results_dir = batch_path.parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_file = results_dir / f"refinement_results_{batch_id}.json"

    with open(out_file, "w", encoding="utf-8") as fp:
        json.dump(summary, fp, indent=2, ensure_ascii=False)

    print("\n" + "=" * 80)
    print(" " * 28 + "EVALUATION SUMMARY")
    print("=" * 80)
    print(f"Total Chunks Evaluated     : {len(chunks)}")
    print(f"Existing Triplets Audited  : {total_audited}")
    print(f"Valid Existing Triplets    : {total_valid} ({total_valid / max(total_audited, 1) * 100:.1f}%)")
    print(f"Invalid / Distorted        : {total_invalid} ({total_invalid / max(total_audited, 1) * 100:.1f}%)")
    print(f"New Missing Triplets Found : {total_missing}")
    print(f"Wall-Clock Time            : {total_time:.2f} s")
    print(f"Checkpoint Log File        : {checkpoint_file}")
    print(f"Final Results Saved To     : {out_file}")
    print("=" * 80 + "\n")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Run LLM Knowledge Graph Refinement & Evaluation across Multi-Node Cluster.")
    parser.add_argument("--batch", type=str, required=True, help="Path to packaged batch JSON file.")
    parser.add_argument("--max-chunks", type=int, default=None, help="Limit number of chunks to evaluate.")
    parser.add_argument("--concurrency", type=int, default=8, help="Number of concurrent requests across cluster workers (default: 8).")
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

    asyncio.run(
        evaluate_batch(
            batch_path=batch_path,
            dispatcher=dispatcher,
            max_chunks=args.max_chunks,
            concurrency=args.concurrency,
            dry_run=args.dry_run
        )
    )


if __name__ == "__main__":
    main()
