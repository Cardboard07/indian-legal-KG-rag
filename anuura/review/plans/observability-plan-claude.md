# Monitoring rollout — first-month plan (wandb + DCGM)

> **Purpose:** stand up visibility into where compute actually goes (GPU vs CPU,
> train vs inference, idle vs active) across Ada and Turing *before* spending on
> network, NAS, or hardware. By end of month we want one self-hosted wandb instance,
> cluster-wide GPU metrics, a dashboard the team can read, and a first baseline report.
>
> **Two independent layers, deliberately:**
> - **wandb** = per-job, opt-in, what the *researcher* logs (loss curves, config, run
>   type). Answers "what is this run doing."
> - **DCGM + Prometheus + Grafana** = per-node, always-on, infrastructure truth (GPU
>   utilisation, memory, power, idle cards). Answers "is the hardware being used."
>
> wandb alone cannot tell you about idle GPUs or jobs that never instrument themselves —
> which is most of the under-utilisation we suspect. DCGM is the layer that catches it.

---

## Prerequisites (before Week 1)

- Named owner for this rollout (the ops/RSE role from infra-recommendations.md #0).
  Without one, this stalls.
- A small always-on VM or host reachable from both clusters' login/head nodes, for the
  wandb server, Prometheus, and Grafana. Modest spec: ~8 vCPU, 32 GB RAM, 500 GB–1 TB
  disk (Prometheus retention is the main consumer). Not a GPU node.
- Agreement from cluster admins to (a) run a node-level exporter on each GPU node and
  (b) open the relevant scrape ports on the internal network only.
- Decide retention up front: Prometheus 30–90 days raw is fine to start; wandb run data
  kept indefinitely.

---

## Week 1 — Stand up the two backends

**Goal:** wandb server reachable; Prometheus scraping a single test node.

1. **Self-host wandb.**
   - Deploy wandb Server (the self-hosted/local offering) on the monitoring host via its
     container image. Put it behind the existing internal auth if available; otherwise
     enable wandb's own user accounts.
   - Confirm a test run from a login node logs successfully:
     `wandb login --host http://<monitor-host>:8080` then a 10-line dummy script.
   - Point storage at a disk with room to grow; back up the wandb metadata volume.
2. **Stand up Prometheus + Grafana** on the same host (two containers).
   - Prometheus with a scrape config; Grafana pointed at Prometheus as a data source.
3. **Install DCGM-exporter on ONE GPU node** of each cluster (one Ada, one Turing) as a
   pilot — ideally as a systemd service or a long-running Slurm job on that node,
   exposing the metrics port to Prometheus only on the internal LAN.
4. Verify Prometheus is scraping `DCGM_FI_DEV_GPU_UTIL`, `DCGM_FI_DEV_FB_USED`
   (framebuffer memory), and power draw from the two pilot nodes.

**Exit criteria:** a dummy wandb run is visible in the UI; Grafana shows live GPU
utilisation for two pilot nodes.

---

## Week 2 — Roll DCGM across all GPU nodes

**Goal:** every GPU node on both clusters reporting; a working "fleet" dashboard.

1. **Deploy DCGM-exporter to all GPU nodes** (the ~90 Ada nodes + 14 Turing nodes).
   Use config management or a templated Slurm/systemd unit so it is reproducible, not
   hand-installed. Account for the mixed driver/CUDA situation (1080 vs 2080/3080 vs
   L40/RTX6000) — confirm DCGM supports the older cards and fall back to
   `nvidia-smi`-based export only where it does not.
2. **Add node labels** in Prometheus: cluster (ada/turing), GPU model, partition. These
   labels are what make the baseline report sliceable later.
3. **Build the core Grafana dashboard:**
   - Fleet GPU utilisation heatmap (per node, per card).
   - Idle-GPU panel: cards allocated but <5% utilisation over a rolling window — this is
     the dev-mode under-utilisation we want to quantify.
   - Memory-used vs memory-available per GPU model.
   - Power draw / rough energy as a cost proxy.
4. **Cross-reference with Slurm:** pull job/account/partition from Slurm (via
   `prometheus-slurm-exporter` or scheduled `sacct` dumps) so utilisation can be
   attributed to accounts and to dev vs burst partitions.

**Exit criteria:** all GPU nodes visible in Grafana; idle-GPU and per-account panels
populated.

---

## Week 3 — Drive wandb adoption and tag run types

**Goal:** enough real runs logging to wandb, tagged so train vs inference is separable.

1. **Make wandb the path of least resistance.** Provide a 1-page quickstart and a copy-
   paste snippet; pre-install the wandb client in the maintained container images
   (infra-recommendations.md #6) with `WANDB_BASE_URL` already pointing at the local
   server.
2. **Mandate a minimal run-type tag.** Ask students to set a job type on every run —
   `train` / `eval` / `inference` / `dev` — via `wandb.init(job_type=...)` or a tag.
   This single field is what lets the baseline split GPU-hours by purpose. Keep it to
   one required field to maximise compliance.
3. **Recruit a few heavy-user labs as early adopters** rather than mandating across all
   137 at once; their runs give the first real signal and surface friction.
4. **Add a Slurm job-submission reminder** (epilog message or wrapper) nudging users to
   log to wandb, so adoption is not purely voluntary memory.

**Exit criteria:** a meaningful share of active runs logging to the local wandb with a
job-type tag; at least the early-adopter labs covered.

---

## Week 4 — Baseline report and review

**Goal:** the first answer to "where does our compute go," plus a decision input for the
network/NAS spend.

1. **Produce a baseline report** covering ~2–3 weeks of data:
   - Overall GPU utilisation distribution, and **% of GPU-hours below a utilisation
     threshold** (the under-utilisation headline number).
   - **Train vs inference vs dev** split of GPU-hours, from wandb job_type cross-
     referenced with DCGM node activity.
   - **Per-account / per-partition** breakdown, including the `nlp`/`irel` no-time-limit
     accounts vs everyone else (fairness check).
   - GPU-memory pressure by card model (does the 11 GB limit actually bind, and where).
   - Rough idle-capacity estimate: how much compute is recoverable via the
     scheduling fixes (dev partition, MPS sharing) in infra-recommendations.md #2.
2. **Wire up basic alerting** (Grafana/Alertmanager): nodes with exporters down, and a
   weekly idle-GPU summary, so the picture stays current after month one.
3. **Hold a review with faculty.** Use the baseline to decide:
   - Whether the suspected under-utilisation is real and how large.
   - Whether the train/inference mix justifies a dedicated inference-serving setup.
   - Whether the network upgrade or NAS should be sized up, down, or deferred.

**Exit criteria:** a written baseline report; a faculty decision on which
infra-recommendations.md items to fund next, now backed by data.

---

## What this explicitly does NOT cover (month 2+)

- Acting on the findings (Slurm partition/MPS changes — separate change window).
- Long-term Prometheus retention / downsampling strategy.
- Cost attribution model for Turing's pay-and-use nodes.
- Full rollout of wandb to all 137 students (beyond early-adopter labs).

---

## One-page summary

| Week | Backend / infra | Adoption | Deliverable |
|------|-----------------|----------|-------------|
| 1 | wandb server + Prometheus/Grafana up; DCGM on 2 pilot nodes | dummy run logs | live 2-node dashboard |
| 2 | DCGM on all GPU nodes; Slurm cross-ref; labels | — | fleet + idle-GPU dashboard |
| 3 | wandb in container images; alerting groundwork | early-adopter labs; job_type tag | tagged real runs flowing |
| 4 | alerting live | broader nudges | **baseline report + faculty decision** |
