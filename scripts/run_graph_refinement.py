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


from src.refinement_contract import SYSTEM_PROMPT, PROMPT_VERSION, validate_evaluation
from src.config import ENTITY_TYPES, PERMITTED_RELATIONSHIP_ENDPOINTS
import hashlib


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
    payload = {
        "chunk": chunk,
        "existing_triplets": [dict(t, relationship_id=t.get("relationship_id") or t.get("local_relationship_id")) for t in existing_triplets],
        "schema": {"entity_types": ENTITY_TYPES, "endpoint_rules": {
            k: {side: sorted(types) for side, types in rule.items()}
            for k, rule in PERMITTED_RELATIONSHIP_ENDPOINTS.items()}},
    }
    return json.dumps(payload, ensure_ascii=False)


async def evaluate_batch(
    batch_path: Path,
    dispatcher: MultiNodeLLMDispatcher,
    max_chunks: Optional[int] = None,
    concurrency: int = 8,
    dry_run: bool = False,
    max_tokens: int = 2048,
    max_prompt_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    with open(batch_path, "r", encoding="utf-8") as f:
        batch_data = json.load(f)

    if concurrency < 1 or max_tokens < 1 or (max_prompt_bytes is not None and max_prompt_bytes < 1) or (max_chunks is not None and max_chunks < 1):
        raise ValueError("concurrency and max_chunks must be positive")
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

    if not dry_run:
        healthy_nodes = await dispatcher.update_health()
        if not healthy_nodes:
            raise RuntimeError(
                f"FATAL CLUSTER ERROR: No healthy LLM worker nodes available in cluster! "
                f"Registered nodes: {len(dispatcher.nodes)}. "
                f"Did the Slurm job hit its time limit or are workers offline? "
                f"Check with 'python cluster/test_cluster.py' or 'squeue -u $USER'."
            )
        print(f"Active Cluster Nodes: {len(healthy_nodes)} | {', '.join(n.id for n in healthy_nodes)}\n")

    # Set up checkpoints directory & file
    checkpoints_dir = batch_path.parent / "checkpoints"
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    model_identity = sorted(list({n.model for n in dispatcher.discover_nodes() if n.model})) if not dry_run else []
    fingerprint = hashlib.sha256((SYSTEM_PROMPT + json.dumps({"batch": batch_data, "models": model_identity,
        "max_tokens": max_tokens, "max_prompt_bytes": max_prompt_bytes}, sort_keys=True)).encode()).hexdigest()
    run_mode = "dry_run" if dry_run else "live"
    checkpoint_file = checkpoints_dir / f"checkpoint_{batch_id}_{run_mode}_{fingerprint[:16]}.jsonl"

    # Load existing checkpoint items to support idempotent resumption
    completed_chunks: Dict[str, Dict[str, Any]] = {}
    if checkpoint_file.exists():
        with open(checkpoint_file, "r", encoding="utf-8") as fp:
            for line in fp:
                line_str = line.strip()
                if line_str:
                    try:
                        record = json.loads(line_str)
                        if record.get("status") == ("dry_run" if dry_run else "success") and record.get("input_hash") == fingerprint:
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
                    rid = t.get("relationship_id") or t.get("local_relationship_id")
                    audited.append({
                        "relationship_id": rid,
                        "is_valid": True,
                        "critique": f"Dry-run verified against verbatim chunk {cid}.",
                        "error_type": "none"
                    })
                mock_eval = {
                    "audited_triplets": audited,
                    "missing_triplets": []
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
                    if not chunk.get("raw_text", "").strip():
                        raise ValueError("Cannot audit without source text")
                    if max_prompt_bytes is not None and len((SYSTEM_PROMPT + prompt).encode("utf-8")) > max_prompt_bytes:
                        raise ValueError("Prompt exceeds byte budget; split the work or explicitly increase budget after checking worker context")
                    resp = await dispatcher.chat_completion(
                        messages=messages,
                        temperature=0.0,
                        max_tokens=max_tokens,
                        response_format={"type": "json_object"}
                    )
                    elapsed = time.time() - t0
                    if resp["choices"][0].get("finish_reason") != "stop":
                        raise ValueError("Incomplete model response (not finish_reason=stop)")
                    content = resp["choices"][0]["message"]["content"]
                    parsed = extract_json_response(content)

                    validate_evaluation(parsed, existing, chunk["raw_text"])

                    res = {
                        "chunk_id": cid,
                        "elapsed_sec": round(elapsed, 2),
                        "evaluation": parsed,
                        "status": "success"
                    }
                except Exception as ex:
                    err_msg = str(ex)
                    # Escalate fatal cluster connection drops immediately!
                    if any(s in err_msg for s in ("No healthy LLM worker", "Cannot start batch: 0", "Connection refused")):
                        raise RuntimeError(f"FATAL CLUSTER ERROR: {err_msg}") from ex
                    res = {
                        "chunk_id": cid,
                        "elapsed_sec": 0.0,
                        "error": err_msg,
                        "status": "failed"
                    }

            res["user_prompt"] = prompt
            res["input_hash"] = fingerprint
            res["prompt_version"] = PROMPT_VERSION
            res["prompt_hash"] = hashlib.sha256((SYSTEM_PROMPT + prompt).encode()).hexdigest()
            if not dry_run and res["status"] == "success":
                res["model"] = resp.get("model")
                res["usage"] = resp.get("usage")
            # Serialize checkpoint writes within this process
            async with file_lock:
                completed_count += 1
                with open(checkpoint_file, "a", encoding="utf-8") as fp:
                    fp.write(json.dumps(res, ensure_ascii=False) + "\n")
                    fp.flush()

                eval_data = res.get("evaluation", {})
                n_audited = len(eval_data.get("audited_triplets", []))
                n_missing = len(eval_data.get("missing_triplets", []))
                status_str = "OK" if res["status"] in ("success", "dry_run") else "ERR"
                err_snippet = f" | ERROR: {res['error'][:60]}" if res.get("error") else ""

                print(
                    f"[{completed_count:3d}/{total_chunks_count:3d}] Chunk {cid[:28]} | "
                    f"Status: {status_str}{err_snippet} | Audited: {n_audited} | Missing: {n_missing} | "
                    f"{res.get('elapsed_sec', 0.0):.2f}s",
                    flush=True
                )

            return res

    # Run pending chunks concurrently
    if pending_chunks:
        tasks = [process_single_chunk(c, i) for i, c in enumerate(pending_chunks, 1)]
        await asyncio.gather(*tasks)

    # Read back complete consolidated results from checkpoint file
    all_records: Dict[str, Dict[str, Any]] = {}
    if checkpoint_file.exists():
        with open(checkpoint_file, "r", encoding="utf-8") as fp:
            for line in fp:
                line_str = line.strip()
                if line_str:
                    try:
                        record = json.loads(line_str)
                        cid = record.get("chunk_id")
                        if not cid:
                            continue
                        # Never overwrite a successful evaluation with a failed record from a crash
                        if cid in all_records and all_records[cid].get("status") in ("success", "dry_run") and record.get("status") not in ("success", "dry_run"):
                            continue
                        all_records[cid] = record
                    except Exception:
                        pass

    # Compile final metrics across the entire batch
    total_audited = 0
    total_valid = 0
    total_invalid = 0
    total_repaired = 0
    total_needs_context = 0
    total_missing = 0
    final_results = []
    failed_chunks = []

    for c in chunks:
        cid = c["chunk_id"]
        rec = all_records.get(cid, {"chunk_id": cid, "status": "unprocessed"})
        final_results.append(rec)
        if rec.get("status") in ("failed", "unprocessed"):
            failed_chunks.append(rec)

        eval_data = rec.get("evaluation", {})
        for a in eval_data.get("audited_triplets", []):
            total_audited += 1
            if a.get("is_valid") is True:
                total_valid += 1
            elif a.get("is_valid") is False:
                total_invalid += 1
                if a.get("corrected_triplet"):
                    total_repaired += 1
            elif a.get("is_valid") is None:
                total_needs_context += 1
        total_missing += len(eval_data.get("missing_triplets", []))

    total_time = time.time() - t_start

    # If chunks failed, abort before saving corrupted final result file
    if failed_chunks:
        first_err = failed_chunks[0].get("error", "Unknown error")
        raise RuntimeError(
            f"Batch {batch_id} incomplete: {len(failed_chunks)}/{len(chunks)} chunks failed! "
            f"First error: {first_err}. Checkpoint preserved at {checkpoint_file.name} for resumption."
        )

    summary = {
        "batch_id": batch_id,
        "input_hash": fingerprint,
        "prompt_version": PROMPT_VERSION,
        "system_prompt": SYSTEM_PROMPT,
        "execution_mode": run_mode,
        "document_title": batch_data.get("document_title", "Unknown"),
        "total_chunks_evaluated": len(chunks),
        "total_audited_triplets": total_audited,
        "valid_triplets": total_valid,
        "invalid_triplets": total_invalid,
        "repaired_triplets": total_repaired,
        "needs_context_triplets": total_needs_context,
        "new_missing_triplets_suggested": total_missing,
        "total_wall_time_sec": round(total_time, 2),
        "results": final_results
    }

    # Save finalized results JSON only on 100% completion
    results_dir = batch_path.parent / ("dry_run_results" if dry_run else "results")
    results_dir.mkdir(parents=True, exist_ok=True)
    out_file = results_dir / f"refinement_results_{batch_id}.json"

    with open(out_file, "w", encoding="utf-8") as fp:
        json.dump(summary, fp, indent=2, ensure_ascii=False)

    print("\n" + "=" * 80)
    print(" " * 28 + "EVALUATION SUMMARY")
    print("=" * 80)
    print(f"Total Chunks Evaluated     : {len(chunks)}")
    print(f"Existing Triplets Audited  : {total_audited}")
    print(f"  - Valid Kept             : {total_valid} ({total_valid / max(total_audited, 1) * 100:.1f}%)")
    print(f"  - Invalid / Distorted    : {total_invalid} ({total_invalid / max(total_audited, 1) * 100:.1f}%)")
    print(f"    * Repaired / Corrected : {total_repaired} ({total_repaired / max(total_invalid, 1) * 100:.1f}% of invalid)")
    print(f"    * Quarantined          : {total_invalid - total_repaired} ({(total_invalid - total_repaired) / max(total_invalid, 1) * 100:.1f}% of invalid)")
    print(f"  - Needs Context (Null)   : {total_needs_context} ({total_needs_context / max(total_audited, 1) * 100:.1f}%)")
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
    parser.add_argument("--max-tokens", type=int, default=2048, help="Maximum response tokens; incomplete responses fail validation.")
    parser.add_argument("--max-prompt-bytes", type=int, default=None, help="Optional input byte budget; unset preserves existing request sizes.")
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
            dry_run=args.dry_run,
            max_tokens=args.max_tokens,
            max_prompt_bytes=args.max_prompt_bytes
        )
    )


if __name__ == "__main__":
    main()
