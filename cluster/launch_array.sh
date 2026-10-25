#!/bin/bash
# ==============================================================================
# Launch Multi-Node LLM Worker Cluster using Slurm Array
# Usage: ./launch_array.sh [NUM_WORKERS] [OPTIONAL_CONSTRAINT]
# Example: ./launch_array.sh 4 l40s
# ==============================================================================

set -euo pipefail

NUM_WORKERS="${1:-2}"
CONSTRAINT="${2:-l40s}"
TIME_LIMIT="${3:-01:00:00}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER_SCRIPT="$SCRIPT_DIR/launch_worker.sh"

echo "=========================================================="
echo "Submitting Slurm Array Job for $NUM_WORKERS LLM Worker Nodes"
echo "Constraint: $CONSTRAINT"
echo "Time Limit: $TIME_LIMIT"
echo "Worker Script: $WORKER_SCRIPT"
echo "=========================================================="

mkdir -p "/home/$USER/llm_cluster/logs" "/home/$USER/llm_cluster/registry"

JOB_ID=$(sbatch --parsable --array=1-"$NUM_WORKERS" --constraint="$CONSTRAINT" --time="$TIME_LIMIT" "$WORKER_SCRIPT")

echo "Slurm Array Job Submitted Successfully!"
echo "Array Job ID: $JOB_ID"
echo ""
echo "Monitor queue with:      squeue -j $JOB_ID"
echo "Check registered nodes:  python3 cluster/test_cluster.py"
echo "Cancel all workers with: scancel $JOB_ID"
echo "=========================================================="
