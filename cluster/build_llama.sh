#!/bin/bash
# ==============================================================================
# Build llama-server natively from source with CUDA on Turing HPC
# Eliminates all GLIBC version mismatches by compiling directly on Ubuntu 22.04
# ==============================================================================

set -e

USER="${USER:-$(whoami)}"
BASE_DIR="/home/$USER/llm_cluster"
BIN_DIR="$BASE_DIR/bin"
SRC_DIR="$BASE_DIR/llama.cpp"

mkdir -p "$BIN_DIR"

echo "=========================================================="
echo "Compiling Native llama-server for Turing (Ubuntu 22.04)"
echo "Target directory: $BIN_DIR"
echo "=========================================================="

# Explicit verified Turing paths for CMake 3.31.7 and CUDA 12.4
export PATH="/usr/local/apps/cmake-3.31.7/bin:/home/u22/cuda-12.4/bin:$PATH"
export LD_LIBRARY_PATH="/home/u22/cuda-12.4/lib64:$LD_LIBRARY_PATH"
export CUDA_HOME="/home/u22/cuda-12.4"
export CUDACXX="/home/u22/cuda-12.4/bin/nvcc"

# Also load environment modules if supported
module load u22/cmake/3.31.7 2>/dev/null || true
module load u22/cuda/12.4 2>/dev/null || true

echo "Compiler & Tooling Check:"
which gcc || echo "gcc: not found"
which g++ || echo "g++: not found"
which cmake || echo "cmake: not found"
which nvcc || echo "nvcc: not found"

if ! command -v nvcc &>/dev/null; then
    echo ""
    echo "ERROR: 'nvcc' (CUDA Compiler) was not found in PATH or environment modules."
    echo "Available CUDA modules on this system:"
    module avail cuda 2>&1 || true
    exit 1
fi

echo "CUDA Compiler Version:"
nvcc --version | grep "release"

# 2. Clone llama.cpp if not present, or pull latest
if [ ! -d "$SRC_DIR" ]; then
    echo ""
    echo "Cloning official ggml-org/llama.cpp repository (shallow clone)..."
    git clone --depth 1 https://github.com/ggml-org/llama.cpp.git "$SRC_DIR"
else
    echo "llama.cpp repository already exists at $SRC_DIR. Updating..."
    cd "$SRC_DIR"
    git pull || true
fi

# 3. Build native llama-server with CUDA
cd "$SRC_DIR"
echo ""
echo "Configuring build with CMake (CUDA enabled)..."
cmake -B build \
    -DGGML_CUDA=ON \
    -DCMAKE_CUDA_ARCHITECTURES="89;75" \
    -DCMAKE_BUILD_TYPE=Release

NUM_CORES=$(nproc 2>/dev/null || echo 8)
echo "Compiling llama-server using $NUM_CORES CPU threads..."
cmake --build build --config Release -j "$NUM_CORES" --target llama-server

# 4. Install binaries and libraries to $BIN_DIR
echo ""
echo "Installing compiled binaries to $BIN_DIR..."
# Remove any conflicting older precompiled binaries from prior runs
rm -f "$BIN_DIR"/llama-* "$BIN_DIR"/*.so* 2>/dev/null || true

cp build/bin/llama-server "$BIN_DIR/"
find build -name "*.so*" -exec cp {} "$BIN_DIR/" \; 2>/dev/null || true
chmod +x "$BIN_DIR/llama-server"

echo ""
echo "=========================================================="
echo "Verification:"
ls -lh "$BIN_DIR/llama-server"
echo "=========================================================="
export LD_LIBRARY_PATH="$BIN_DIR:$LD_LIBRARY_PATH"
"$BIN_DIR/llama-server" --version
echo "=========================================================="
echo "Native Turing llama-server compiled and verified successfully!"
