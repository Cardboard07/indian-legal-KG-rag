# Changes made in this chat

All changes are local, uncommitted, on refining. Nothing was deployed to HPC.
No model files, extraction records, live graph, clustering or evaluation data were
intentionally changed. Reading SQLite may create transient WAL/SHM sidecar files.

## Modified files

| File | Change |
|---|---|
| cluster/cluster_dispatcher.py | Fix double decrement of active request counts; parse explicit URL host/port correctly. |
| cluster/launch_array.sh | Stop on submission errors and create registry/log directories before sbatch. Existing arguments/resources preserved. |
| cluster/launch_worker.sh | Keep supervising shell alive, track/stop server and health-check children, clean registry on exit, publish registry by rename. Original setup/download fallback, model selection, CUDA flags and Slurm resources preserved. |
| cluster/test_cluster.py | Add optional --verify/--min-nodes for distinct hosts and JSON inference pinned to each worker. Existing status/benchmark commands preserved. |
| scripts/package_evaluation_batch.py | Fail on missing/unreadable/empty source; fall back to portable relative chunk path. Original min-triplets=1 default preserved; --min-triplets 0 remains available. |
| scripts/run_graph_refinement.py | Use shared strengthened prompt; include complete triplet fields; require complete schema-valid audits and finish_reason=stop; separate mock/live checkpoints/results; fingerprint input/prompt/settings/registry models; record prompts/model/usage; support null validity; configurable output tokens and optional input-byte cap; propagate unexpected task exceptions. |
| scripts/reconcile_refinement_results.py | Refuse mock/legacy-unidentified results, skip failed chunks, use read-only preview, check relationship ownership and boolean types, accept only exact full quotes and legal schema, use stable proposal IDs and skip identical replay. |
| src/config.py | Add five-second timeout to Windows WSL host detection. Existing Linux host/environment behavior preserved. |
| .gitignore | Ignore mock outputs, SQLite WAL/SHM sidecars and the generated handoff zip. |

## Added files

| File | Purpose |
|---|---|
| src/refinement_contract.py | Shared prompt and validation for complete audit coverage, abstention, allowed endpoint types, quotes and qualifier structures. |
| tests/test_refinement.py | 11 offline regression tests, including asynchronous/checkpoint checks. Eight synchronous checks passed locally; full suite awaits HPC. |
| cluster/requirements-refinement.txt | Optional minimal dependency list, not a request to reinstall the existing environment. |
| .gitattributes | Keep shell scripts LF on checkout. |
| REFINEMENT_REVIEW.md | Detailed architecture review, findings, fixes and remaining limitations. |
| HPC_CHECKS.md | Commands for the existing HPC checkout/environment/workers. |
| CHANGES_REFINEMENT.md | This change inventory. |
| refinement_hpc_review.zip | Generated source/document handoff bundle; ignored by Git. Not deployed; do not overwrite HPC-specific edits blindly. |

## Adjustments after your HPC clarification

Restored original worker auto-setup/download fallback; restored min-triplets=1;
made the input-byte cap opt-in; fixed per-node verification so registry discovery
cannot route a node-specific check to a different worker. Replaced fresh-install
instructions with commands for the existing installation.

## Verification

Eight synchronous regression tests passed. Python compilation and git diff
whitespace checks passed. Asynchronous tests, Slurm worker lifecycle, GPU offload,
container behavior and live model output have not been verified on HPC. This is
not a claim that the entire deployment has no regressions.
