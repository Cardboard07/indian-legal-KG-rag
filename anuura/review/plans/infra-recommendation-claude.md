# Infrastructure recommendations

> **Goal:** increase the *effective per-capita* compute available to LTRC (≈8 faculty,
> 137 research students) and improve *productivity*, on shared university compute
> (Ada + Turing), without assuming new hardware until the data justifies it.

Recommendations are ordered by return on investment: near-zero-cost scheduling and
visibility changes first, then network, then storage, then portability/cloud. A
funded operations owner (Recommendation 0) is a prerequisite for everything below.

---

## 0. Fund a dedicated ops owner (prerequisite)

Every recommendation here depends on a competent, funded sysadmin / research-software
engineer who owns the shared caches, container images, Slurm config, and monitoring.
With 145 users and no dedicated ops role, caches go stale, images rot, and any NAS
purchased becomes an unmaintained liability. Write this in as a headcount line item,
not an aside.

---

## 1. Get visibility before spending (monitoring)

We currently do not know the GPU-vs-CPU split, nor the train-vs-inference split within
GPU usage. We should not size storage, network, or hardware purchases until we do.

1. **Self-host wandb** and give all students access, as already planned, to capture
   per-job usage patterns.
2. **Add cluster-level metrics** that wandb alone will not give: DCGM-exporter +
   Prometheus + Grafana for node-level GPU utilisation, memory use, and idle-GPU
   detection across both clusters.
3. Run for several weeks, then let the numbers size every other investment below. If a
   large fraction of GPU-hours turns out to be inference, a small dedicated
   inference-serving setup (e.g. vLLM with batching) may beat any storage purchase on
   per-capita throughput.

*Cost: low. This is the cheapest way to avoid mis-spending on the items below.*

---

## 2. Fix scheduling and utilisation (Slurm) — cheapest capacity available

Under-utilisation from dev jobs and poorly-tuned burst jobs is largely a scheduling
problem, addressable in Slurm config without new hardware.

1. **Dev/interactive partition on the oldest GPUs.** Put dev-mode work on the
   CUDA-limited 1080 nodes with short time limits and aggressive preemption. This is
   the "hardware on standby to test code" pattern, and it stops debugging sessions from
   occupying scarce 3080/L40/RTX6000 capacity.
2. **GPU sharing for dev jobs via NVIDIA MPS.** A debugging session does not need a
   whole 11 GB card; MPS lets several low-utilisation dev jobs share one GPU, directly
   raising effective per-capita compute. (MIG is unsupported on these consumer cards;
   MPS works.)
3. **Backfill + fair-share + preemption** so burst jobs soak idle capacity and no
   account starves others. Revisit the `nlp`/`irel` accounts that currently hold 12
   GPUs with no time limit — a fairness asymmetry across 137 students.
4. **Re-examine the 4-GPU-per-user cap.** Confirm whether it is a hardware ceiling or a
   QoS / `MaxTRESPerUser` policy. If it is policy, raising it for well-formed multi-GPU
   burst jobs (while keeping dev jobs capped) can unlock the scaling that hardware
   availability should already permit. This costs nothing.

*Cost: near zero. Likely the single biggest per-capita gain.*

---

## 3. Upgrade the interconnect before committing to a storage design

The 1 Gbps LAN is the stated root constraint behind slow downloads and slow
private↔public transfers, and it is shared with Ada/Turing disk traffic. **Decide the
storage architecture only after answering: can we get a faster link?**

1. Price a 10/25 Gbps upgrade, at minimum between any central storage and the two
   clusters.
2. A faster interconnect may deliver more effective per-capita compute than a NAS
   itself, and it changes the correct storage design — so resolve it first.

---

## 4. Data and model caching

Directly targets repeated internet downloads and the dependency-time problem; partially
relieves the LAN bottleneck by removing internet round-trips.

1. **Read-only shared cache**, populated once and mounted everywhere: a HuggingFace hub
   mirror, common datasets, and base model weights.
2. **Per-user scratch** kept separate from the shared cache.
3. **Locality matters.** Keep the shared cache as close to compute as possible. If it
   sits on a NAS reached over the same 1 Gbps LAN, the bottleneck moves rather than
   disappears (see Recommendation 3).

---

## 5. Storage (NAS) — right instinct, execute carefully

A central NAS for LTRC is reasonable, but the naive "everything reads from the NAS over
the LAN" design worsens the very bottleneck we are trying to fix.

1. **Buy a NAS as central LTRC storage, but replicate — do not live-mount — to
   dedicated spaces on Ada and Turing.** Be explicit about mechanism:
   - *Replicate:* read-only caches and base weights, via scheduled rsync/snapshot, so a
     job never blocks on the NAS or the LAN.
   - *Keep node-local:* active scratch and training checkpoints (high-churn,
     latency-sensitive).
2. **Redundancy.** The NAS becomes a single point of failure for 145 people; budget RAID
   plus at least one second/offsite copy.

---

## 6. Dependency management via containers

Right diagnosis (students lose significant time to dependencies on low disk space),
with a correction on tooling for shared HPC.

1. **Use Apptainer/Singularity, not Docker.** Slurm HPC nodes generally cannot run
   Docker (needs root); Apptainer is rootless and reads images from the shared
   filesystem.
2. **Maintain a small set of versioned base images:** CUDA 11.x for the 1080 nodes, a
   newer CUDA for 2080/3080/L40/RTX6000. Students layer on top.
3. Portable images also ease moving workloads to external cloud when needed
   (Recommendation 7).

---

## 7. Workload portability and cloud burst overflow

Separate the two workload types and make burst work both *movable* and *scalable*.

1. **Dev-mode:** low-utilisation, latency-sensitive editing/debugging — served by the
   shared dev partition (Recommendation 2) and GPU sharing.
2. **Burst-mode:** run-to-finish jobs that should run on any compute, internal or
   external. A portable container plus a thin Slurm-to-cloud submission path (e.g.
   SkyPilot-style) lets us offload burst jobs near conference deadlines, exactly when
   on-prem availability collapses.
3. **Keep steady-state on-prem (cheaper); rent only the deadline spikes.**

---

## 8. Reduce training inefficiency (people + tooling)

Some waste is in the jobs themselves: fp16 runs on Ada taking up to 4 days, burst jobs
with un-tuned batch sizes.

1. Ship maintained containers with mixed precision, gradient checkpointing, and (where
   supported) flash-attention configured correctly.
2. Provide a short internal guide — batch size, gradient accumulation, choosing a
   partition, avoiding idle GPUs — and brief training for the 137 students. Tooling
   alone will not fix poorly-optimised jobs.

---

### Suggested sequencing

1. Recommendation 0 (ops owner) and Recommendation 1 (monitoring) immediately.
2. Recommendation 2 (Slurm) in parallel — near-zero cost, large gain.
3. Recommendation 3 (network) decision, which gates Recommendations 4–5.
4. Recommendations 4–6 once data and network direction are known.
5. Recommendations 7–8 as ongoing productivity work.
