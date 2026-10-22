# Inference and training benchmarks

List of models run on ada and turing with throughputs and other details documented.

| Hardware | Model | pp | tg | Code | Dev notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| ada 3x2080ti, 1x3080 | Qwen3-30B-A3B-Thinking-2507 Q6_K  | pp2048 2096.62 ± 289.67 | tg32 94.96 ± 0.91 | [scripts](https://github.com/d3vdru/anuura/blob/main/bechmarks/ada/run-job.sh) | @ojas.kataria: ada is on cuda 12.8, vllm has moved onto 13.x, hence using llama.cpp, benchmark done using [llama-benchy](https://github.com/eugr/llama-benchy) |
| turing 1xRTX 6000 | Qwen3.6-27B-UD-Q8_K_XL | pp2048 1344.82 ± 35.48 | tg32 24.40 ± 0.08 | [scripts](https://github.com/d3vdru/anuura/blob/main/bechmarks/turing/run-job.sh) | hosted using llama.cpp, tested using llama-benchy
turing 1xRTX 6000 | DeepSeek-R1-Distill-Llama-70B-UD-Q4_K_XL |pp2048 639.32 ± 53.76 | tg32 16.75 ± 0.01 | ^ | ^