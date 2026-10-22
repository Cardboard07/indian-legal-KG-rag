#!/bin/bash

#SBATCH --job-name=llama.cpp_gguf
#SBATCH --partition=u22
#SBATCH --constraint=rtx6000
# ^^^ Adjust constraint to rtx6000 or l40s depending on your needs.
#SBATCH --gres=gpu:1
# ^^^ Adjust based upon size of the model you are testing.
# Each RTX 6000 or L40S has 48GB VRAM (compared to 11GB on Ada's RTX 2080Ti).
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=01:00:00
## ^^^ Adjust based on your usage
#SBATCH --output=/home/%u/llama_%j.log
# ^^^ Using %u placeholder as Slurm doesn't expand $USER in directives

# 1. Definitions
USER="${USER:-$(whoami)}" # If auto detect fails, then set manually
GGUF_PATH="/home/$USER/Qwen3.6-27B-UD-Q8_K_XL.gguf"

# 2. Load singularity
source /etc/profile
module load u22/singularity-ce

# 3. Launch the llama.cpp server
# Since /home is mounted on the compute nodes, we can run the model directly from home
# without needing a local scratch transfer. We bind /home to ensure it is accessible inside the container.
echo "Starting llama.cpp server..."

singularity exec --nv \
  -B /home:/home \
  /home/$USER/llama_server/llama-cpp-cuda.sif \
bash -c 'export LD_LIBRARY_PATH=/app:/usr/local/lib:/usr/local/lib64:$LD_LIBRARY_PATH && /app/llama-server -m /home/'"$USER"'/Qwen3.6-27B-UD-Q8_K_XL.gguf --fit on -fitt 128 -np 1 --host 0.0.0.0 --port 8080'