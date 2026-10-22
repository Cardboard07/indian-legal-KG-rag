## Current state of GPU infrastructure.

1. Ada has about 40 nodes with older 1080 GPUs and another 50 nodes with 2080/3080 GPUs.
    1. Outdated GPUs: 1080 only supports older versions of CUDA and PyTorch.
    2. GPU memory: Each GPU card comes with 11GB GPU memory and they can request up to 4 GPUs.
        1. The total usable memory is up to 44 GPU which itself is a constraint for very large models but training/inference of large models can be _usually_ distributed into 4 GPUs. There are two scenarios (all 4 GPUs are from same gnode vs different ones):
            1. Distribute to multiple GPUs within a gnode: this is feasible
                1. However, with large layers or context length is long, this distribution errors out with OOM.
            2. Distribute to multiple GPUs across gnodes: not feasible due to network bottleneck
            3. Achievable maximum model by parameter count with distribution on 4 GPUs:
                1. Inference: 7B param at fp32 or 10B param at fp16
                2. Training: 8B param at fp16
    3. Turing comparison: In comparison, Turing (that has a pay-and-use model) has 14 nodes of L40/RTX6000 (10/4 nodes each with 4 cards) cards with 48GB memory with requests maxing to 4 like Ada.
        1. For comparison, 8B param fp32 model that takes 1d on Turing to finetune runs only at fp16 on Ada and takes up to 4d if it completes without errors.
        2. They still miss out on optimisations available for B/H-series cars like mxfp4/mxfp8, flash-attention, etc. B/H-series cards also have larger GPU memory from 48G to 96G.
    4. Limits on wall-time and availability: 
        1. Ada and Turing have a 4d wall-time limit for their jobs.
        2. Accounts nlp and irel have access to 12 GPUs with no time limits.
        3. GPU availability: There is a broad concern over poor availability closer to conference deadlines.
    5. Turing has nvlink but neither RTX nor L40 support it. Multinode training can still be done over NCCL/InfiniBand (througputs to be tested), but pooled memory is not possible without NVLinks.
## Storage, data and environment management.

1. Disk space on Ada and Turing.
    1. Private: They both run disks mounted on specific gnodes that have low network turnarounds but is limited to 100G (share1) and 30G (home) that persists.
    2. Public: Additionally 1T common pool space (tmp) is available through NAS for which data has to go over slower network with a 7-day auto-delete policy.
    3. Data transfer between private and public has to be over the network (scp) and is slow.
2. Data and environment management.
    1. All PyTorch and related libraries sit in home taking up limited space from the _home_ making dependancy management slow.
    2. Any data has to be downloaded into _share1_ necessarily from external sources (hence slow) since there is no place to persist large data.
    3. The only space left for model/data cache is _share1_ and very likely is repeatedly downloaded due to lack of common local cache.

