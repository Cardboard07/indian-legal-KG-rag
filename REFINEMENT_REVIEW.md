# Repository and refinement review â€” 26 October 2026

Branch verified: `refining`; working tree was clean before this review.

## Scope and architecture

Reviewed the extraction/schema/storage pipeline, entity resolution and export,
FalkorDB ingestion, projection and recursive Leiden hierarchy, report packing and
generation, retrieval and answer synthesis, evaluation harnesses, refinement
scripts, cluster provisioning/dispatch, demo and anuura infrastructure references.
Inspected representative packaged chunks and queried local SQLite counts read-only.
This is a source/data review, not a claim that every source passage has been
semantically audited or that live FalkorDB/Turing deployment has passed.

Local SQLite contains 1,080 documents, 140,296 chunks, 571,326 entity mentions,
438,670 relationships and 157,589 entity resolutions. All relationships have
review_status=unreviewed.

The flow is manifest -> regex extraction -> SQLite -> canonical entity resolution
-> FalkorDB source/entity/assertion/evidence layers -> weighted undirected
projection -> recursive Leiden -> community reports -> local/global retrieval.
`anuura` is independent infrastructure documentation; `cluster` is the actual
multi-worker serving and dispatch implementation. Multiple workers are replicas
processing separate requests, not tensor-parallel execution of one model.

## Critical findings

1. **Answer accuracy is not established.** `src/answer_generator.py` has canned
   branches matching pilot questions, including numeric conclusions independent
   of retrieved source support. `src/global_search.py` uses lexical overlap and
   templated reduction, not LLM map/reduce. `src/report_generator.py` uses selected
   assertions and templates, not LLM synthesis. The extraction prompt in
   `src/prompt.py` is not what the regex extractor sends to a model: that extractor
   does not make an LLM call. Configuration model labels do not prove inference.
2. **Evaluation scores are proxies.** `src/global_evaluate.py:compute_eval_scores`
   rewards context length, citation count and keywords rather than correctness
   against reference answers. Its text baseline is similarly templated. README
   benchmark numbers should not be treated as validated answer-quality results.
3. **Refinement was vulnerable to false success.** Dry-run records shared live
   checkpoints; missing audit arrays defaulted to empty and counted as success;
   output booleans and identities were unchecked. Fixed by distinct outputs,
   input/prompt/settings/model-registry fingerprints and a strict shared contract.
4. **Quote verification could certify fabricated suffixes.** Reconciliation
   accepted only the first 40 matching characters and fabricated the remaining
   offsets. Fixed: the entire quote must occur exactly. A quote match still does
   not prove that the proposed relation follows from it; human sampling remains
   necessary. Original `src/validator.py` separately has a normalized-match path
   that assigns offset zero; this pre-existing extraction issue remains.
5. **Refinement does not synchronize the authoritative live graph.** Reconciliation
   writes SQLite, not FalkorDB. Ingestion resets relationship review status to
   unreviewed; it does not filter quarantined rows, remove stale direct edges,
   or invalidate communities/reports. Do not apply refinement and assume live
   answers use it. Implement a versioned reviewed graph rebuild or explicit
   synchronization/removal before production use. This remains open.

## Refinement changes made

- Shared prompt in `src/refinement_contract.py`: schema endpoint rules, all entity
  types and predicates (including EntityCategory and ISSUES), exact full quotes,
  preservation of modality/scope/qualifiers, no unsupported inference or invented
  examples, one audit per supplied global relationship ID, and null/needs_context
  for insufficient source context.
- User prompt now includes all supplied triplet fields, including numeric and
  temporal qualifiers and evidence. Previously those qualifiers were omitted.
- Reject incomplete generation, partial audit coverage, unknown/duplicate IDs,
  string booleans, invalid endpoint types and malformed qualifier structures.
- Save exact user prompt, system prompt, prompt version/hash, model and usage
  when reported. Live checkpoint keys include source/settings/registry identity.
  Model content changes at the same path/URL still require fresh checkpoints.
- Dry runs never manufacture missing compliance rules and cannot be reconciled.
  Legacy result files without execution_mode=live are deliberately refused.
- Reconciliation previews use a read-only SQLite connection; apply uses stable
  content-derived proposal IDs, avoids replaying identical proposals and rejects
  unknown relationship IDs and invalid schema instead of coercing types.
- Packaging fails on missing/empty text, supports the portable relative chunk
  path, and supports zero-triplet documents with --min-triplets 0; the original default of 1 is preserved.
- Fixed double decrement of dispatcher active request counts and parsing of
  URLs without explicit ports.
- Worker keeps its supervising shell, tracks child processes, removes registry
  files on exit and publishes registry files by atomic rename. The original
  native-binary/container setup and download fallbacks are preserved.
- Submission creates log directories before sbatch and stops on submission
  failure. Shell files have LF attributes. WSL host detection now has a timeout.
- Added --verify --min-nodes to check distinct hosts and JSON generation from
  every worker, separate from the load-balanced throughput benchmark.

## Remaining limitations before a full run

- Partitioning currently groups chunks by document and slice, then audits one
  chunk per request. It is not an arbitrary graph-edge partition with the union
  of all provenance chunks. Existing relationship evidence may cite other chunks;
  neighboring paragraphs/cross-reference text are not automatically supplied.
  Null/needs_context avoids forced rejection, but context assembly needs a second
  pass. Standalone entity audits are not a separate output; only triplet endpoint
  identities/types are audited. New nodes are proposed as relation endpoints.
- Chunk slicing bounds file size by chunk count, not model tokens. A conservative
  optional input-byte guard (--max-prompt-bytes) can fail oversized prompts explicitly.
  The default remains unlimited for compatibility with the existing deployment. It does not replace
  checking the actual model tokenizer/context and output budget. There is no
  automatic splitting or model-output repair/retry loop. Failed chunks can resume.
- Two Slurm array tasks can land on the same physical host. --verify --min-nodes 2
  deliberately rejects that as proof of multi-node readiness. Request distinct
  nodes using the site's supported scheduling policy if necessary.
- GPU model fit, CUDA driver/runtime compatibility and effective per-slot context
  need measurement on the allocated hardware. Health/JSON inference cannot prove
  GPU offload: check server logs and nvidia-smi while inference is running.
- Health-check readiness polling is finite; dynamic port selection has a small
  bind race. Static URL metadata says Default rather than discovering model ID.
  Dispatcher global concurrency is not a hard per-worker slot cap, and repeated
  HTTP errors need better node-specific backoff. Run conservative concurrency first.
- Build/download scripts still use mutable upstream releases/tags and hardcoded
  cluster paths; setup's hardcoded model-size check is not a cryptographic check.
  Pin and record model checksum and llama.cpp version for scientific comparisons.
- `requirements.txt` omits runtime dependencies used by major modules, including
  pydantic, sentence-transformers, igraph and leidenalg. A small dedicated
  `cluster/requirements-refinement.txt` supplies the refinement dependencies.
- Reconciliation is an optional importer of model suggestions, not semantic
  human approval. Proposal hash deduplication is exact-content only; semantically
  equivalent existing relations and changed wording can still create duplicates.
  It does not populate specialized Requirement details. Keep results as proposals
  until reviewed. No production SQLite/FalkorDB contents were modified here.
- Report generation caps L1 reports at 50 and skips L2; a complete clustering
  frontier does not imply complete report coverage. Report findings are taken
  from assertion samples independently of the packed context, so manifest
  inclusion statistics do not fully describe generated finding provenance.
- Snapshot hashing defaults to the first 100 MB; the database is about 2.77 GB.
  It is not a full-database checksum and can miss changes outside that prefix.
- Baseline dense scoring only reranks lexical candidates; it is not an
  independent dense corpus retrieval. Regulator is stored on the retriever but
  does not filter its FTS SQL. Local graph expansion is outgoing and bounded by
  global query limits rather than the documented per-node fanout/PPR behavior.

## Validation status and proposed HPC sequence

Python source compilation and git whitespace checks passed locally. Added offline
regression tests for audit completeness, boolean types, context abstention,
endpoint legality, fabricated quotes, qualifier shapes, prompt field retention,
URL defaults, dry/live checkpoint isolation and active-request accounting.
The complete suite could not be confirmed in this Windows restricted environment:
Eight synchronous refinement regression tests passed on recheck. The asynchronous
and filesystem-backed suite remains unconfirmed: asyncio hangs even on an empty
event loop, and default temporary directories
reported write-denied errors. A requested unrestricted run was declined. Run the
suite on Turing using the existing environment as shown in HPC_CHECKS.md.
Existing Stage 3 tests require live FalkorDB and should be run separately against
a development snapshot, not presumed to be offline tests.

Recommended order: per-node health and GPU offload -> per-node JSON inference ->
concurrent benchmark -> synthetic regression tests -> a few real refinement chunks
-> manually assess precision, missing-relation yield and needs_context/failure
rates -> improve context assembly -> scale. Compare before/after on a held-out,
human-annotated set using triplet precision/recall, qualifier correctness and
citation entailment. Do not use the current proxy answer scores as that measure.
