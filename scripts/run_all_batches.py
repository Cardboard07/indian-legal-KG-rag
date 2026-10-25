"""Overnight Multi-Batch Refinement Runner.
Dispatches evaluation across a collection of packaged batches using active Turing cluster nodes.
Supports checkpoint-level resumption, automatic batch queuing, and aggregate reporting.

Usage:
    # 1. Run all batches in data/eval_batches with concurrency 16
    python scripts/run_all_batches.py --concurrency 16

    # 2. Run with pattern filter (e.g. only part1 to part10 or specific regulator)
    python scripts/run_all_batches.py --pattern "eval_batch_RBI_JUL22_RBI_*.json" --concurrency 16
"""

import os
import sys
import re
import json
import time
import asyncio
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from cluster.cluster_dispatcher import MultiNodeLLMDispatcher
from scripts.run_graph_refinement import evaluate_batch


def natural_sort_key(s: Path) -> List[Any]:
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", s.name)]


def is_batch_complete(batch_path: Path) -> bool:
    """Checks if a batch already has a complete, error-free results file."""
    try:
        with open(batch_path, "r", encoding="utf-8") as fp:
            batch_data = json.load(fp)
        batch_id = batch_data.get("batch_id")
        total_chunks = len(batch_data.get("chunks", []))

        results_file = batch_path.parent / "results" / f"refinement_results_{batch_id}.json"
        if not results_file.exists():
            return False

        with open(results_file, "r", encoding="utf-8") as fp:
            res_data = json.load(fp)

        # Check execution mode and chunk count
        if res_data.get("execution_mode") != "live":
            return False

        results_list = res_data.get("results", [])
        if len(results_list) < total_chunks:
            return False

        # Check that no chunk has status 'failed'
        has_failed = any(r.get("status") == "failed" for r in results_list)
        return not has_failed
    except Exception:
        return False


async def run_overnight_queue(
    batch_dir: Path,
    pattern: str = "*.json",
    concurrency: int = 16,
    max_tokens: int = 2048,
    max_prompt_bytes: Optional[int] = None,
    delay_between_batches: float = 1.0,
    reset: bool = False,
    registry_dir: Optional[str] = None,
    urls: Optional[str] = None,
):
    explicit_urls = [u.strip() for u in urls.split(",") if u.strip()] if urls else None
    dispatcher = MultiNodeLLMDispatcher(registry_dir=registry_dir, explicit_urls=explicit_urls)

    print("Checking LLM cluster connectivity before queue startup...")
    healthy_nodes = await dispatcher.update_health()
    if not healthy_nodes:
        print("\n" + "!" * 85)
        print("FATAL ERROR: 0 healthy LLM worker nodes found in cluster!")
        print("Cannot start overnight queue without active inference workers.")
        print("Check cluster health with: python cluster/test_cluster.py")
        print("Launch workers with      : bash cluster/launch_array.sh 4 l40s 08:00:00")
        print("!" * 85 + "\n")
        return
    print(f"Cluster Ready: {len(healthy_nodes)} healthy worker node(s) online: {', '.join(n.id for n in healthy_nodes)}\n")

    # Find matching batch files
    all_files = sorted(list(batch_dir.glob(pattern)), key=natural_sort_key)
    # Filter out result and checkpoint files if placed in the same folder
    batch_files = [f for f in all_files if not f.name.startswith(("refinement_results_", "checkpoint_")) and f.is_file()]

    if not batch_files:
        print(f"No matching batch files found in {batch_dir} with pattern '{pattern}'.")
        return

    if reset:
        print("[RESET] Clearing previous checkpoints and results in batch directory...")
        for p in (batch_dir / "checkpoints").glob("*.jsonl"):
            p.unlink()
        for p in (batch_dir / "results").glob("*.json"):
            p.unlink()
        print("[RESET] Clean state ready.")

    log_file = batch_dir / "overnight_run.log"

    cum_chunks = 0
    cum_audited = 0
    cum_valid = 0
    cum_invalid = 0
    cum_missing = 0
    consecutive_failures = 0
    t_start_total = time.time()

    for idx, bpath in enumerate(batch_files, 1):
        if is_batch_complete(bpath):
            print(f"[{idx:2d}/{len(batch_files):2d}] SKIPPING (Already Completed): {bpath.name}")
            continue

        print(f"\n>>> [{idx:2d}/{len(batch_files):2d}] LAUNCHING BATCH: {bpath.name}")
        t0 = time.time()

        try:
            summary = await evaluate_batch(
                batch_path=bpath,
                dispatcher=dispatcher,
                concurrency=concurrency,
                max_tokens=max_tokens,
                max_prompt_bytes=max_prompt_bytes,
                dry_run=False,
            )
            elapsed = time.time() - t0
            consecutive_failures = 0

            chunks_cnt = summary.get("total_chunks_evaluated", 0)
            audited_cnt = summary.get("total_audited_triplets", 0)
            valid_cnt = summary.get("valid_triplets", 0)
            invalid_cnt = summary.get("invalid_triplets", 0)
            missing_cnt = summary.get("new_missing_triplets_suggested", 0)

            cum_chunks += chunks_cnt
            cum_audited += audited_cnt
            cum_valid += valid_cnt
            cum_invalid += invalid_cnt
            cum_missing += missing_cnt

            log_entry = (
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Batch {idx}/{len(batch_files)} ({bpath.name}): "
                f"Chunks={chunks_cnt}, Audited={audited_cnt}, Valid={valid_cnt}, Invalid={invalid_cnt}, "
                f"Missing={missing_cnt}, WallTime={elapsed:.1f}s\n"
            )
            with open(log_file, "a", encoding="utf-8") as fp:
                fp.write(log_entry)

            print(
                f"\n>>> [{idx:2d}/{len(batch_files):2d}] BATCH COMPLETE ({elapsed:.1f}s) | "
                f"Cumulative Chunks: {cum_chunks} | Audited: {cum_audited} | "
                f"Invalid: {cum_invalid} | New Missing: {cum_missing}"
            )

        except Exception as ex:
            consecutive_failures += 1
            err_str = str(ex)
            print(f"\n[ERROR] Failed processing batch {bpath.name}: {ex}")
            with open(log_file, "a", encoding="utf-8") as fp:
                fp.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Batch {bpath.name} FAILED: {ex}\n")

            if any(s in err_str for s in ("FATAL CLUSTER ERROR", "No healthy LLM worker", "Cannot start batch: 0")):
                print("\n" + "!" * 85)
                print("FATAL: LLM Cluster outage detected! Halting overnight queue runner immediately.")
                print("Please check Turing worker status: python cluster/test_cluster.py")
                print("To launch new 8-hour workers    : bash cluster/launch_array.sh 4 l40s 08:00:00")
                print("!" * 85 + "\n")
                break

            if consecutive_failures >= 3:
                print("\n" + "!" * 85)
                print(f"CIRCUIT BREAKER TRIGGERED: {consecutive_failures} consecutive batches failed!")
                print("Aborting queue runner to prevent runaway failures.")
                print("!" * 85 + "\n")
                break

        if delay_between_batches > 0:
            await asyncio.sleep(delay_between_batches)

    total_wall_time = time.time() - t_start_total

    print("\n" + "=" * 85)
    print(" " * 28 + "OVERNIGHT QUEUE FINISHED")
    print("=" * 85)
    print(f"Total Batches Processed      : {len(batch_files)}")
    print(f"Total Chunks Evaluated       : {cum_chunks}")
    print(f"Total Triplets Audited       : {cum_audited}")
    print(f"  - Valid Kept               : {cum_valid} ({cum_valid / max(cum_audited, 1) * 100:.1f}%)")
    print(f"  - Invalid / Distorted      : {cum_invalid} ({cum_invalid / max(cum_audited, 1) * 100:.1f}%)")
    print(f"Total New Triplets Discovered: {cum_missing}")
    print(f"Total Queue Wall Time        : {total_wall_time / 60.0:.2f} minutes")
    print(f"Master Run Log               : {log_file}")
    print("=" * 85 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Run overnight queue across multiple refinement batches.")
    parser.add_argument("--batch-dir", type=str, default="data/eval_batches", help="Directory containing batch JSON files.")
    parser.add_argument("--pattern", type=str, default="*.json", help="Glob pattern for batches (default: *.json).")
    parser.add_argument("--concurrency", type=int, default=16, help="Concurrent requests across cluster (default: 16).")
    parser.add_argument("--max-tokens", type=int, default=2048, help="Maximum response tokens per request (default: 2048).")
    parser.add_argument("--max-prompt-bytes", type=int, default=None, help="Optional maximum prompt bytes.")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds to pause between batches.")
    parser.add_argument("--reset", action="store_true", help="Clear prior checkpoints and results in batch directory before starting.")
    parser.add_argument("--registry-dir", type=str, default=None, help="Cluster registry directory.")
    parser.add_argument("--urls", type=str, default="", help="Comma-separated explicit URLs.")

    args = parser.parse_args()
    batch_dir = Path(args.batch_dir)
    if not batch_dir.exists():
        print(f"Error: Directory not found: {batch_dir}")
        return

    asyncio.run(
        run_overnight_queue(
            batch_dir=batch_dir,
            pattern=args.pattern,
            concurrency=args.concurrency,
            max_tokens=args.max_tokens,
            max_prompt_bytes=args.max_prompt_bytes,
            delay_between_batches=args.delay,
            reset=args.reset,
            registry_dir=args.registry_dir,
            urls=args.urls,
        )
    )


if __name__ == "__main__":
    main()
