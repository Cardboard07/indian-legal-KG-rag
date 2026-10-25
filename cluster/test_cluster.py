#!/usr/bin/env python3
"""Diagnostic & Benchmark CLI for Multi-Node LLM Cluster.
Usage:
    python test_cluster.py            # Check status and health of all cluster nodes
    python test_cluster.py --bench    # Run concurrent inference benchmark across all nodes
"""

import os
import sys
import time
import asyncio
import argparse
import json

# Ensure cluster directory and root are on sys.path
sys.path.insert(0, os.path.abspath("."))
sys.path.insert(0, os.path.abspath("cluster"))

from cluster.cluster_dispatcher import MultiNodeLLMDispatcher


def print_cluster_status(dispatcher: MultiNodeLLMDispatcher, healthy_nodes):
    all_nodes = list(dispatcher.nodes.values())
    print("\n" + "=" * 80)
    print(" " * 26 + "LLM CLUSTER STATUS REPORT")
    print("=" * 80)
    print(f"Registry Path : {dispatcher.registry_dir}")
    print(f"Total Nodes   : {len(all_nodes)}")
    print(f"Healthy Nodes : {len(healthy_nodes)}")
    print("-" * 80)
    print(f"{'Node Hostname':<22} | {'Port':<6} | {'GPU':<18} | {'Status':<10} | {'Latency':<9}")
    print("-" * 80)

    for node in all_nodes:
        status_str = "READY" if node.is_healthy else "OFFLINE"
        lat_str = f"{node.last_latency_ms:.1f} ms" if node.is_healthy else "N/A"
        print(f"{node.hostname:<22} | {node.port:<6} | {node.gpu[:18]:<18} | {status_str:<10} | {lat_str:<9}")

    print("=" * 80 + "\n")


async def run_benchmark(dispatcher: MultiNodeLLMDispatcher, num_requests: int = 8):
    healthy = dispatcher.get_healthy_nodes()
    if not healthy:
        print("ERROR: Cannot run benchmark without healthy nodes.")
        return

    print(f"Running concurrent benchmark: {num_requests} requests across {len(healthy)} healthy node(s)...")

    test_prompt = [
        {"role": "system", "content": "You are a legal regulatory AI assistant. Be concise."},
        {"role": "user", "content": "What is the primary role of the Reserve Bank of India in one sentence?"}
    ]

    batch_inputs = [test_prompt for _ in range(num_requests)]

    t0 = time.time()
    results = await dispatcher.batch_chat_completions(batch_inputs, max_tokens=100, temperature=0.0)
    elapsed = time.time() - t0

    succeeded = [r for r in results if r is not None]
    print("\n" + "=" * 80)
    print(" " * 28 + "BENCHMARK RESULTS")
    print("=" * 80)
    print(f"Total Requests      : {num_requests}")
    print(f"Succeeded           : {len(succeeded)} / {num_requests}")
    print(f"Total Wall Time     : {elapsed:.2f} s")
    print(f"Throughput          : {len(succeeded) / max(elapsed, 0.001):.2f} req/s")
    print("-" * 80)

    # Print sample generation
    if succeeded:
        sample_choice = succeeded[0].get("choices", [{}])[0]
        sample_text = sample_choice.get("message", {}).get("content", "").strip()
        print(f"Sample LLM Output:\n\"{sample_text}\"")

    print("-" * 80)
    print("Per-Node Performance:")
    for node in healthy:
        print(f"  - {node.id}: completed={node.total_completed}, errors={node.total_errors}")
    print("=" * 80 + "\n")


async def main_async():
    parser = argparse.ArgumentParser(description="Test and monitor LLM cluster status.")
    parser.add_argument("--registry-dir", type=str, default=None, help="Path to registry directory containing node JSONs.")
    parser.add_argument("--verify", action="store_true", help="Require healthy workers and valid JSON inference on every worker.")
    parser.add_argument("--min-nodes", type=int, default=2, help="Minimum healthy workers for --verify.")
    parser.add_argument("--bench", action="store_true", help="Run concurrent inference benchmark across nodes.")
    parser.add_argument("--num-requests", type=int, default=8, help="Number of benchmark requests to dispatch.")
    parser.add_argument("--urls", type=str, default="", help="Comma-separated static URLs to test (e.g. http://10.4.1.2:8080).")

    args = parser.parse_args()

    explicit_urls = [u.strip() for u in args.urls.split(",") if u.strip()] if args.urls else None
    dispatcher = MultiNodeLLMDispatcher(registry_dir=args.registry_dir, explicit_urls=explicit_urls)

    print("Discovering and checking health of cluster nodes...")
    healthy = await dispatcher.update_health()
    print_cluster_status(dispatcher, healthy)

    if args.verify:
        if len(healthy) < args.min_nodes:
            raise SystemExit(f"Verification failed: need {args.min_nodes} healthy workers (found {len(healthy)})")
        async def verify_node(node):
            worker = MultiNodeLLMDispatcher(explicit_urls=[node.url], request_timeout=180)
            # Pin this check to this node; registry discovery could select another worker.
            worker.nodes = {node.url: node}
            result = await worker.chat_completion(
                messages=[{"role": "user", "content": 'Return only this JSON object: {"ready": true}'}],
                temperature=0, max_tokens=128, response_format={"type": "json_object"})
            choice = result["choices"][0]
            if choice.get("finish_reason") != "stop" or json.loads(choice["message"]["content"]) != {"ready": True}:
                raise ValueError(f"Invalid inference from {node.id}")
            print(f"Verified inference: {node.id} | model={result.get('model', 'unreported')}")
        await asyncio.gather(*(verify_node(node) for node in healthy))
        print("All workers passed JSON inference. Check worker logs/nvidia-smi separately for GPU offload.")

    if args.bench:
        await run_benchmark(dispatcher, num_requests=args.num_requests)


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\nInterrupted.")


if __name__ == "__main__":
    main()
