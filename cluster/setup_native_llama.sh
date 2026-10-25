#!/bin/bash
# ==============================================================================
# Setup Native Precompiled llama.cpp with CUDA 12.8 for IIITH Turing
# Downloads official standalone precompiled binaries (with bundled CUDA runtime)
# Size: ~566 MB (Takes ~15-30 seconds to download and extract)
# Zero Singularity container needed! Zero quota issues!
# ==============================================================================

set -e

USER="${USER:-$(whoami)}"
BASE_DIR="/home/$USER/llm_cluster"
BIN_DIR="$BASE_DIR/bin"
mkdir -p "$BIN_DIR"

echo "=========================================================="
echo "Setting up Native CUDA llama-server on Turing"
echo "Target directory: $BIN_DIR"
echo "=========================================================="

TAG="b11871"
TAR_NAME="cudart-llama-${TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz"
DOWNLOAD_URL="https://github.com/ggml-org/llama.cpp/releases/download/${TAG}/${TAR_NAME}"

LLAMA_SERVER_BIN="$BIN_DIR/llama-server"

if [ -f "$LLAMA_SERVER_BIN" ]; then
    echo "llama-server binary already exists at: $LLAMA_SERVER_BIN"
    ls -lh "$LLAMA_SERVER_BIN"
else
    echo "Downloading official precompiled CUDA 12.8 binary (~566 MB)..."
    echo "URL: $DOWNLOAD_URL"
    TAR_FILE="/tmp/${USER}_${TAR_NAME}"

    wget --continue --show-progress -O "$TAR_FILE" "$DOWNLOAD_URL"

    echo "Extracting binary and CUDA libraries to $BIN_DIR..."
    tar -xzf "$TAR_FILE" -C "$BIN_DIR"
    rm -f "$TAR_FILE"

    chmod +x "$BIN_DIR"/llama-* 2>/dev/null || true
    echo "Extraction complete!"
fi

echo ""
echo "=========================================================="
echo "Verification:"
ls -lh "$BIN_DIR"/llama-server
echo "=========================================================="
echo "Native llama-server is ready to run!"
