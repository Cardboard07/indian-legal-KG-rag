# Multi-Node LLM Cluster Setup on IIITH Turing

This directory provides the scripts and Python dispatcher to run and orchestrate distributed local LLMs (Qwen 2.5 32B Instruct) across GPU nodes on the IIITH Turing HPC cluster.

---

## Architecture Overview

```
                               ┌────────────────────────────────────────────────────────┐
                               │     Turing Login Node / Orchestrator Workstation       │
                               │  (MultiNodeLLMDispatcher / test_cluster.py / Evaluator)│
                               └───────────────────────────┬────────────────────────────┘
                                                           │
                                   ┌───────────────────────┴───────────────────────┐
                                   │  Watches /home/$USER/llm_cluster/registry/    │
                                   ▼                                               ▼
     ┌─────────────────────────────────────────────────┐   ┌─────────────────────────────────────────────────┐
     │       Slurm Node A (e.g. L40S 48GB VRAM)        │   │      Slurm Node B (e.g. RTX 6000 48GB VRAM)     │
     │ ┌─────────────────────────────────────────────┐ │   │ ┌─────────────────────────────────────────────┐ │
     │ │ Singularity: llama.cpp CUDA Container       │ │   │ │ Singularity: llama.cpp CUDA Container       │ │
     │ │ Model: Qwen 2.5 32B Instruct (Q6_K)         │ │   │ │ Model: Qwen 2.5 32B Instruct (Q6_K)         │ │
     │ │ Context: 32,768 tokens (full GPU offload)   │ │   │ │ Context: 32,768 tokens (full GPU offload)   │ │
     │ │ API: http://10.4.x.x:PORT/v1/chat/completions│ │   │ │ API: http://10.4.y.y:PORT/v1/chat/completions│ │
     │ └─────────────────────────────────────────────┘ │   │ └─────────────────────────────────────────────┘ │
     │   Registers: registry/node_1001_1.json          │   │   Registers: registry/node_1001_2.json          │
     │   (Auto-cleans on exit/cancellation)            │   │   (Auto-cleans on exit/cancellation)            │
     └─────────────────────────────────────────────────┘   └─────────────────────────────────────────────────┘
```

---

## Step 1: Initial Setup on Turing (One-time)

SSH into Turing:
```bash
ssh <your_username>@turing.iiit.ac.in
```

Navigate to your workspace on Turing and run the environment setup script:
```bash
bash cluster/setup_env.sh
```
This script:
1. Creates directory hierarchy under `/home/$USER/llm_cluster/` (`models/`, `containers/`, `registry/`, `logs/`).
2. Loads `u22/singularity-ce`.
3. Pulls the official `llama.cpp:server-cuda` Singularity container image.
4. Downloads **Qwen 2.5 32B Instruct (Q6_K GGUF)** (~26.4 GB) directly to `/home/$USER/llm_cluster/models/`.

---

## Step 2: Launch LLM Worker Nodes

To submit a Slurm array job launching `N` worker nodes (e.g. 2 nodes on `l40s` or `rtx6000`):
```bash
bash cluster/launch_array.sh 2 "l40s|rtx6000"
```

Each worker:
- Allocates 1 GPU (48GB VRAM), 8 CPU cores, and 48GB RAM on partition `u22`.
- Finds an available dynamic TCP port.
- Writes an atomic registration JSON file in `/home/$USER/llm_cluster/registry/node_<JOBID>_<TASKID>.json`.
- Runs `llama-server` inside Singularity with 32k context and full GPU offload (`-ngl 99`).
- Automatically unregisters its JSON file when the job completes or is cancelled via `trap cleanup EXIT`.

---

## Step 3: Verify Cluster Status & Benchmark

Install `aiohttp` if not present:
```bash
pip install aiohttp
```

Check the health and latency of all active cluster nodes:
```bash
python3 cluster/test_cluster.py
```

Expected output:
```text
================================================================================
                          LLM CLUSTER STATUS REPORT
================================================================================
Registry Path : /home/<user>/llm_cluster/registry
Total Nodes   : 2
Healthy Nodes : 2
--------------------------------------------------------------------------------
Node Hostname          | Port   | GPU                | Status     | Latency  
--------------------------------------------------------------------------------
turing-node04          | 38421  | NVIDIA L40S        | READY      | 4.2 ms   
turing-node07          | 41209  | Quadro RTX 6000    | READY      | 5.1 ms   
================================================================================
```

Run a concurrent multi-node inference benchmark:
```bash
python3 cluster/test_cluster.py --bench --num-requests 8
```
This dispatches test legal queries across all healthy nodes and reports wall time, throughput (req/s), and per-node completed count.

---

## Step 4: Python Integration

In downstream scripts (such as the upcoming Triplet Evaluation script), simply import `MultiNodeLLMDispatcher`:

```python
import asyncio
from cluster.cluster_dispatcher import MultiNodeLLMDispatcher

async def main():
    dispatcher = MultiNodeLLMDispatcher()
    
    # Send a prompt to the least-loaded healthy node
    response = await dispatcher.chat_completion(
        messages=[
            {"role": "system", "content": "You are a regulatory legal analyzer."},
            {"role": "user", "content": "Extract missing relations from this text..."}
        ],
        temperature=0.0
    )
    print(response["choices"][0]["message"]["content"])

asyncio.run(main())
```

---

## Slurm Job Management

- **View queue**: `squeue -u $USER`
- **View logs**: `tail -f /home/$USER/llm_cluster/logs/worker_*.log`
- **Cancel a specific job**: `scancel <JOB_ID>`
- **Cancel all workers**: `scancel -u $USER -n llm_worker`
