#!/bin/bash
# ==============================================================================
# Slurm Worker Script for Multi-Node LLM Cluster on IIITH Turing
# Runs a dedicated llama-server instance on an allocated GPU node (RTX 6000 / L40S)
# Dynamically registers itself in /home/$USER/llm_cluster/registry/
# ==============================================================================

#SBATCH --job-name=llm_worker
#SBATCH --partition=u22
#SBATCH --constraint=l40s
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=08:00:00
#SBATCH --output=/home/%u/llm_cluster/logs/worker_%j.log
#SBATCH --error=/home/%u/llm_cluster/logs/worker_%j.err

set -e

USER="${USER:-$(whoami)}"
BASE_DIR="/home/$USER/llm_cluster"
REGISTRY_DIR="$BASE_DIR/registry"
MODELS_DIR="$BASE_DIR/models"
CONTAINER_PATH="$BASE_DIR/containers/llama-cpp-cuda.sif"

# Auto-detect GGUF model if not explicitly specified (filter for valid >1GB files)
if [ -z "$MODEL_PATH" ]; then
    DETECTED_MODEL=$(find "$MODELS_DIR" -maxdepth 1 -name "*.gguf" -size +1G 2>/dev/null | head -n 1)
    MODEL_PATH="${DETECTED_MODEL:-$MODELS_DIR/Qwen2.5-32B-Instruct-Q6_K.gguf}"
fi

# Ensure directories exist
mkdir -p "$REGISTRY_DIR" "$BASE_DIR/logs"

export PATH="/home/u22/cuda-12.4/bin:$PATH"
export LD_LIBRARY_PATH="/home/u22/cuda-12.4/lib64:$BASE_DIR/bin:$LD_LIBRARY_PATH"
module load u22/cuda/12.4 2>/dev/null || true

# Check for Native CUDA binary first (preferred over Singularity)
NATIVE_BIN="$BASE_DIR/bin/llama-server"

if [ ! -x "$NATIVE_BIN" ] && [ ! -f "$CONTAINER_PATH" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [ -f "$SCRIPT_DIR/setup_native_llama.sh" ]; then
        echo "[Worker] Native binary not found. Running setup_native_llama.sh..."
        bash "$SCRIPT_DIR/setup_native_llama.sh" || true
    fi
fi

# Auto-detect existing container or pull if missing and native binary not present
if [ ! -x "$NATIVE_BIN" ]; then
    if [ ! -f "$CONTAINER_PATH" ] && [ -f "/home/$USER/llama_server/llama-cpp-cuda.sif" ]; then
        echo "[Worker] Found existing container in ~/llama_server/llama-cpp-cuda.sif. Linking..."
        mkdir -p "$(dirname "$CONTAINER_PATH")"
        ln -sf "/home/$USER/llama_server/llama-cpp-cuda.sif" "$CONTAINER_PATH"
    fi

    if [ ! -f "$CONTAINER_PATH" ]; then
        echo "[Worker] Container image not found at $CONTAINER_PATH. Pulling on compute node using /tmp scratch..."
        mkdir -p "$(dirname "$CONTAINER_PATH")"

        # Free any partial cache in /home to stay under 50GB quota
        rm -rf "$HOME/.singularity/cache" 2>/dev/null || true

        # Direct temporary cache & layer extraction to node-local /tmp (prevents home quota exhaustion)
        export SINGULARITY_TMPDIR="/tmp/${USER}_sing_tmp_$$"
        export SINGULARITY_CACHEDIR="/tmp/${USER}_sing_cache_$$"
        mkdir -p "$SINGULARITY_TMPDIR" "$SINGULARITY_CACHEDIR"
        export GODEBUG=http2client=0

        echo "[Worker] Pulling container with temporary dirs in /tmp..."
        singularity pull "$CONTAINER_PATH" docker://ghcr.io/ggml-org/llama.cpp:server-cuda || true
        rm -rf "$SINGULARITY_TMPDIR" "$SINGULARITY_CACHEDIR" 2>/dev/null || true
    fi
fi

# Determine Job and Task ID
JOB_ID="${SLURM_JOB_ID:-manual_$$}"
TASK_ID="${SLURM_ARRAY_TASK_ID:-0}"
REG_FILE="$REGISTRY_DIR/node_${JOB_ID}_${TASK_ID}.json"

SERVER_PID=""
HEALTH_PID=""
cleanup() {
    trap - EXIT INT TERM
    if [ -n "$HEALTH_PID" ]; then
        kill "$HEALTH_PID" 2>/dev/null || true
        wait "$HEALTH_PID" 2>/dev/null || true
    fi
    if [ -n "$SERVER_PID" ]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -f "$REG_FILE" "$REG_FILE.tmp"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Detect Node IP and Hostname
NODE_HOSTNAME=$(hostname)
NODE_IP=$(hostname -I | awk '{print $1}')

# Find an available port dynamically
PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('', 0)); print(s.getsockname()[1]); s.close()")
GPU_NAME=$(nvidia-smi --query-gpu=gpu_name --format=csv,noheader | head -n 1 | xargs)

echo "=========================================================="
echo "Starting LLM Worker on $NODE_HOSTNAME ($NODE_IP)"
echo "Job ID: $JOB_ID | Task ID: $TASK_ID | GPU: $GPU_NAME"
echo "Port:   $PORT"
echo "Model:  $MODEL_PATH"
echo "=========================================================="

# Write initial starting status to registry
cat <<EOF > "$REG_FILE.tmp"
{
  "job_id": "$JOB_ID",
  "task_id": "$TASK_ID",
  "hostname": "$NODE_HOSTNAME",
  "ip": "$NODE_IP",
  "port": $PORT,
  "url": "http://$NODE_IP:$PORT",
  "gpu": "$GPU_NAME",
  "model": "$MODEL_PATH",
  "started_at": $(date +%s),
  "status": "starting"
}
EOF
mv "$REG_FILE.tmp" "$REG_FILE"

# Background health-checker to mark ready once server is responding
(
    for i in $(seq 1 60); do
        sleep 2
        if curl -s "http://127.0.0.1:$PORT/health" | grep -q "ok" || curl -s "http://127.0.0.1:$PORT/v1/models" | grep -q "object"; then
            echo "[Worker $JOB_ID.$TASK_ID] llama-server is healthy! Updating status to ready."
            cat <<EOF > "$REG_FILE.tmp"
{
  "job_id": "$JOB_ID",
  "task_id": "$TASK_ID",
  "hostname": "$NODE_HOSTNAME",
  "ip": "$NODE_IP",
  "port": $PORT,
  "url": "http://$NODE_IP:$PORT",
  "gpu": "$GPU_NAME",
  "model": "$MODEL_PATH",
  "started_at": $(date +%s),
  "status": "ready"
}
EOF
mv "$REG_FILE.tmp" "$REG_FILE"
            break
        fi
    done
) &
HEALTH_PID=$!

# Run llama-server inside Singularity with full GPU offload and OpenAI API endpoint
# Context size: 32,768 tokens (full legal instrument context)
# Run llama-server with full GPU offload and OpenAI API endpoint
# Context size: 32,768 tokens (full legal instrument context)
# -ngl 99: offload all transformer layers to GPU
# --parallel 2: process up to 2 concurrent generation slots per node

if [ -x "$NATIVE_BIN" ]; then
    echo "[Worker] Launching NATIVE CUDA llama-server binary directly..."
    export LD_LIBRARY_PATH="/home/u22/cuda-12.4/lib64:$BASE_DIR/bin:$LD_LIBRARY_PATH"
    "$NATIVE_BIN" \
      -m "$MODEL_PATH" \
      --ctx-size 32768 \
      -ngl 99 \
      --parallel 2 \
      --flash-attn on \
      --host 0.0.0.0 \
      --port "$PORT" &
else
    echo "[Worker] Launching llama-server inside Singularity container..."
    singularity exec --nv \
      -B /home:/home \
      "$CONTAINER_PATH" \
      bash -c "export LD_LIBRARY_PATH=/app:/usr/local/lib:/usr/local/lib64:\$LD_LIBRARY_PATH && \
        /app/llama-server \
          -m \"$MODEL_PATH\" \
          --ctx-size 32768 \
          -ngl 99 \
          --parallel 2 \
          --host 0.0.0.0 \
          --port $PORT" &
fi
SERVER_PID=$!
wait "$SERVER_PID"
