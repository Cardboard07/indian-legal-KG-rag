#!/bin/bash
# ==============================================================================
# Setup Environment for Multi-Node LLM Cluster on IIITH Turing
# Run this once on the login node or inside an interactive session (srun)
# ==============================================================================

set -e

USER="${USER:-$(whoami)}"
BASE_DIR="/home/$USER/llm_cluster"
MODELS_DIR="$BASE_DIR/models"
CONTAINER_DIR="$BASE_DIR/containers"
REGISTRY_DIR="$BASE_DIR/registry"
LOGS_DIR="$BASE_DIR/logs"

echo "=========================================================="
echo "Initializing LLM Cluster directories under /home/$USER"
echo "=========================================================="
mkdir -p "$MODELS_DIR" "$CONTAINER_DIR" "$REGISTRY_DIR" "$LOGS_DIR"

# 1. Attempt to load Singularity module if available in environment
echo "Checking Singularity availability..."
if [ -f /etc/profile.d/modules.sh ]; then
    source /etc/profile.d/modules.sh
fi
source /etc/profile 2>/dev/null || true

if command -v module &>/dev/null; then
    module load u22/singularity-ce 2>/dev/null || module load singularity 2>/dev/null || true
fi

# 2. Check / Pull official llama.cpp CUDA container
CONTAINER_PATH="$CONTAINER_DIR/llama-cpp-cuda.sif"
if [ -f "$CONTAINER_PATH" ]; then
    echo "Found existing Singularity container: $CONTAINER_PATH"
elif command -v singularity &>/dev/null; then
    echo "Pulling llama.cpp CUDA container from GitHub Container Registry..."
    singularity pull "$CONTAINER_PATH" docker://ghcr.io/ggml-org/llama.cpp:server-cuda
    echo "Container pulled successfully: $CONTAINER_PATH"
elif command -v apptainer &>/dev/null; then
    echo "Pulling llama.cpp CUDA container using Apptainer..."
    apptainer pull "$CONTAINER_PATH" docker://ghcr.io/ggml-org/llama.cpp:server-cuda
    echo "Container pulled successfully: $CONTAINER_PATH"
else
    echo "--------------------------------------------------------------------------"
    echo "NOTE: 'singularity' runtime is not available on this login node."
    echo "On Turing, Singularity is loaded on the GPU compute nodes ('u22' partition)."
    echo "The container will be automatically pulled by the Slurm worker job on the"
    echo "compute node, or you can pull it inside an interactive compute session:"
    echo "   srun -p u22 -C l40s --pty bash -l"
    echo "Proceeding with model download on the login node..."
    echo "--------------------------------------------------------------------------"
fi

# 3. Model Download: Qwen 2.5 32B Instruct (Q6_K)
# Model size: ~26.4 GB (fits with full 32k context into 48GB VRAM on RTX 6000 / L40S)
MODEL_NAME="qwen2.5-32b-instruct-q6_k.gguf"
MODEL_PATH="$MODELS_DIR/$MODEL_NAME"

echo ""
echo "=========================================================="
echo "Checking model: $MODEL_NAME"
echo "Target path: $MODEL_PATH"
echo "=========================================================="

if [ -f "$MODEL_PATH" ]; then
    echo "Model already exists at: $MODEL_PATH"
    ls -lh "$MODEL_PATH"
else
    echo "Model not found. Downloading Qwen2.5-32B-Instruct-GGUF (Q6_K)..."
    HF_REPO="Qwen/Qwen2.5-32B-Instruct-GGUF"
    HF_URL="https://huggingface.co/Qwen/Qwen2.5-32B-Instruct-GGUF/resolve/main/$MODEL_NAME"

    # Check if huggingface-cli is available, else use wget or curl
    if command -v huggingface-cli &> /dev/null; then
        echo "Using huggingface-cli..."
        huggingface-cli download "$HF_REPO" "$MODEL_NAME" --local-dir "$MODELS_DIR" --local-dir-use-symlinks False
    elif command -v wget &> /dev/null; then
        echo "Using wget with resume support..."
        wget --continue --show-progress -O "$MODEL_PATH" "$HF_URL"
    elif command -v curl &> /dev/null; then
        echo "Using curl with resume support..."
        curl -L -C - -o "$MODEL_PATH" "$HF_URL"
    else
        echo "ERROR: Neither huggingface-cli, wget, nor curl is installed."
        exit 1
    fi

    echo "Model downloaded successfully to: $MODEL_PATH"
    ls -lh "$MODEL_PATH"
fi

echo ""
echo "=========================================================="
echo "Environment setup complete!"
echo "Directories:"
echo "  - Models:    $MODELS_DIR"
echo "  - Container: $CONTAINER_PATH"
echo "  - Registry:  $REGISTRY_DIR"
echo "  - Logs:      $LOGS_DIR"
echo "=========================================================="
