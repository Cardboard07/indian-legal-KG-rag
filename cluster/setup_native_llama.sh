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
BIN_TAR="llama-${TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz"
BIN_URL="https://github.com/ggml-org/llama.cpp/releases/download/${TAG}/${BIN_TAR}"

CUDART_TAR="cudart-llama-${TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz"
CUDART_URL="https://github.com/ggml-org/llama.cpp/releases/download/${TAG}/${CUDART_TAR}"

LLAMA_SERVER_BIN="$BIN_DIR/llama-server"

# Step 1: Download & extract main binaries (llama-server, llama-cli, libggml-cuda.so)
if [ -f "$LLAMA_SERVER_BIN" ]; then
    echo "llama-server binary already exists at: $LLAMA_SERVER_BIN"
else
    echo "Downloading official precompiled CUDA 12.8 llama binaries (~178 MB)..."
    echo "URL: $BIN_URL"
    TAR_FILE="/tmp/${USER}_${BIN_TAR}"

    wget --continue --show-progress -O "$TAR_FILE" "$BIN_URL"

    echo "Extracting binary to $BIN_DIR..."
    tar -xzf "$TAR_FILE" --strip-components=1 -C "$BIN_DIR"
    rm -f "$TAR_FILE"

    chmod +x "$BIN_DIR"/llama-* 2>/dev/null || true
    echo "Binaries extracted!"
fi

# Step 2: Ensure CUDA runtime libraries (libcudart.so, libcublas.so) are in $BIN_DIR
# Check if cudart subfolder was already extracted from prior run
if compgen -G "$BIN_DIR/cudart-llama-*" > /dev/null; then
    echo "Moving existing CUDA runtime libraries to $BIN_DIR..."
    mv "$BIN_DIR"/cudart-llama-*/*.so* "$BIN_DIR"/ 2>/dev/null || true
    rm -rf "$BIN_DIR"/cudart-llama-*
fi

# If libcudart is still missing, download the cudart bundle
if [ ! -f "$BIN_DIR/libcudart.so.12" ]; then
    echo "Downloading CUDA 12.8 runtime libraries (~566 MB)..."
    CUDART_TMP="/tmp/${USER}_${CUDART_TAR}"
    wget --continue --show-progress -O "$CUDART_TMP" "$CUDART_URL"
    tar -xzf "$CUDART_TMP" --strip-components=1 -C "$BIN_DIR"
    rm -f "$CUDART_TMP"
fi

echo ""
echo "=========================================================="
echo "Verification:"
ls -lh "$BIN_DIR"/llama-server
echo "=========================================================="
export LD_LIBRARY_PATH="$BIN_DIR:$LD_LIBRARY_PATH"
"$BIN_DIR"/llama-server --version || true
echo "=========================================================="
echo "Native llama-server is ready to run!"
