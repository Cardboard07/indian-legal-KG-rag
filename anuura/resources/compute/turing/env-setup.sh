MODEL_URL=""
USER="${USER:-$(whoami)}" # If auto detect fails, then set manually

# Request an interactive session on a Turing GPU node (RTX 6000 or L40S)
# Adjust partition (-p) and constraint (-C) based on your cluster's settings
# Example constraints: rtx6000 or l40s
srun -p u22 -C l40s --pty bash -l

cd /home/$USER
mkdir -p llama_server && cd llama_server
module load u22/singularity-ce

# Pull the official llama.cpp CUDA container
singularity pull llama-cpp-cuda.sif docker://ghcr.io/ggml-org/llama.cpp:server-cuda

# Download a quantized GGUF model (Example: Llama-3-8B Q4)
# !! This will overwrite model.gguf if it exists
wget --continue --show-progress $MODEL_URL -O model.gguf
