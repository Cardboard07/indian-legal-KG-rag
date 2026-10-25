# Checks using the existing HPC installation

Do not create a new checkout, reinstall packages, recreate the environment,
replace model files, or restart existing workers for these initial checks.
Activate the Python environment you normally use and run from the existing repo.
Local changes in this chat are uncommitted and are not automatically on HPC.
Do not unzip the review bundle over an HPC checkout with unreviewed local edits.

## First: existing code and workers (works before syncing local changes)

```bash
# Run from your existing Knowledge_graph directory with your usual environment active.
pwd
git branch --show-current
git status --short
command -v python
python --version
squeue -u "$USER"
python cluster/test_cluster.py
python cluster/test_cluster.py --bench --num-requests 4
```

Send the output. Do not change branches or start duplicate workers to make these
commands pass. If there are no workers, the queue/assets/status output determines
which existing launch command to use. A Slurm RUNNING job may still be loading.
The benchmark uses inference but does not write the graph/database.

## After the reviewed local changes have been synced to the existing checkout

This step requires src/refinement_contract.py and tests/test_refinement.py as well
as the edited scripts; do not copy only the runner. Preserve HPC-specific edits
when merging. No package installation is needed unless the import check identifies
a missing dependency. aiohttp is optional because urllib is already supported.

```bash
python -c "import pydantic; print('pydantic', pydantic.__version__)"
python -m unittest tests.test_refinement tests.test_workflow -v
python cluster/test_cluster.py --verify --min-nodes 2
python cluster/test_cluster.py --bench --num-requests 8
```

The verification command requires two distinct hosts and performs JSON inference
on every healthy registered worker. Check GPU offload separately in worker logs
or nvidia-smi on allocated nodes using the site's supported access method.

## Small real pilot, using an existing batch

```bash
find data/eval_batches -maxdepth 1 -type f -name '*.json' | head -n 5
# Set this to one of your existing batch paths:
BATCH="data/eval_batches/YOUR_EXISTING_BATCH.json"
python scripts/run_graph_refinement.py --batch "$BATCH" --max-chunks 2 --dry-run
python scripts/run_graph_refinement.py --batch "$BATCH" --max-chunks 2 --concurrency 2
```

No packaging or re-extraction is needed if the existing batch has correct source
text. Mock results go to dry_run_results; live results go to results. Both write
result/checkpoint files, but neither changes the extraction database or FalkorDB.
Send the live JSON/error output. Do not use reconciliation --apply yet.
The input-byte cap is opt-in; output tokens remain at the existing default 2048.

## Compatibility changes to know about

- Existing commands, default paths, model selection, CUDA flags and Slurm resources
  are preserved. Worker provisioning fallback and min-triplets=1 are preserved.
- Refined live checkpoints have new input/prompt fingerprints; old checkpoints
  will not be reused. This may repeat inference, intentionally avoiding mock/stale
  results. New result filenames still use the existing batch ID, so copy any
  previous results you want to retain before rerunning that batch.
- Live output must now include every supplied global relationship ID exactly
  once. Incomplete or malformed responses fail instead of silently succeeding.
- Legacy result files without execution_mode=live cannot be reconciled; do not
  relabel historical mock files as live. Null validity means needs_context.
- Packager now fails on missing/unreadable/empty source instead of auditing
  placeholder text. It first uses the existing path, then a relative-path fallback.
