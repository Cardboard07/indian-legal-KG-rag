#!/bin/bash

#SBATCH --job-name=llama.cpp_gguf
#SBATCH --partition=u22
#SBATCH --constraint=2080ti
#SBATCH --gres=gpu:4
# ^^^ Adjust based upon size of the model you are testing, for referece each RTX 2080Ti has 11GB VRAM
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=12:00:00
## ^^^ Adjust on your usage
#SBATCH --output=/home2/%u/llama_%j.log
# ^^^ Using %u placeholder as Slurm doesn't expand $USER in directives

# 1. Definitions
USER="${USER:-$(whoami)}" # If auto detect fails, then set manually
GGUF_PATH="/ssd_scratch/$USER/model.gguf"

# 2. Define the exact path to your local GGUF file
mkdir -p /ssd_scratch/$USER/

# 3. Load singularity
source /etc/profile
module load u22/singularity-ce

# 4. Transfer the model from share1 to ssd_scratch
# For this step you need to have passwordless ssh set up
echo "Transferring model from /share1 to /ssd_scratch..."
rsync -avP $USER@ada:/share1/$USER/model.gguf $GGUF_PATH
echo "Transfer complete!"

# 5. Launch the llama.cpp server
echo "Starting llama.cpp server..."

singularity exec --nv \
  -B /ssd_scratch:/ssd_scratch \
  /home2/$USER/llama_server/llama-cpp-cuda.sif \
bash -c 'export LD_LIBRARY_PATH=/app:/usr/local/lib:/usr/local/lib64:$LD_LIBRARY_PATH && /app/llama-server -m /ssd_scratch/'"$USER"'/model.gguf --fit on -fitt 128 -np 1 --host 0.0.0.0 --port 8080'
