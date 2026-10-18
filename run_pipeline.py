"""Main execution CLI and workflow orchestrator for Regulatory Knowledge Graph Extraction.
Commands:
  manifest : Build corpus manifest and sync to SQLite database
  pilot    : Run calibration pilot on 150 representative chunks across MCA, RBI, SEBI
  extract  : Run bounded batch extraction on pending corpus chunks
  resolve  : Run conservative cross-chunk entity resolution
  export   : Generate GraphRAG/FalkorDB JSONL exports
  verify   : Run comprehensive integrity and completeness verification
  status   : Display real-time progress and queue status
"""

import sys
import argparse
import json
import time
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

from src.config import (
    DB_PATH,
    MANIFEST_PATH,
    PILOT_DIR,
    EXPORTS_DIR,
    MODEL_NAME,
    EXECUTION_ROUTE,
)
from src.manifest import ManifestBuilder
from src.db import ExtractionStore, get_db_connection, compute_cache_key
from src.schema import ChunkExtractionOutput
from src.validator import ExtractionValidator
from src.extractor import ExtractionCoordinator, SemanticLegalExtractor
from src.entity_resolver import ConservativeEntityResolver
from src.exporter import GraphRAGExporter
from src.verifier import CorpusVerifier


def cmd_manifest(args):
    print("=== Step 1: Building Complete Corpus Manifest ===")
    t0 = time.time()
    manifest = ManifestBuilder.build_manifest(MANIFEST_PATH, max_chunks=args.max_chunks)
    store = ExtractionStore(DB_PATH)
    print("Syncing manifest into SQLite database...")
    store.sync_manifest(manifest)
    elapsed = time.time() - t0
    print(f"Manifest build & sync completed in {elapsed:.2f}s.")
    cmd_status(args)


def cmd_pilot(args):
    print("=== Step 2: Running Calibration Pilot on Representative Sample ===")
    store = ExtractionStore(DB_PATH)
    coordinator = ExtractionCoordinator(store)

    run_id = f"pilot_run_{int(time.time())}"
    store.create_run(run_id, notes="Calibration pilot on 150 representative corpus chunks across MCA, RBI, SEBI.")

    # Select representative chunks across MCA, RBI, and SEBI
    pilot_chunks = []
    with get_db_connection(DB_PATH) as conn:
        for reg in ["MCA", "RBI", "SEBI"]:
            # Pick 50 chunks from each regulator across diverse documents
            cursor = conn.execute("""
                SELECT c.*, s.processing_status
                FROM manifest_chunks c
                JOIN chunk_processing_state s ON c.chunk_id = s.chunk_id
                WHERE c.regulator = ? AND c.char_count > 30
                ORDER BY c.folder_id, c.chunk_index ASC
                LIMIT 50;
            """, (reg,))
            pilot_chunks.extend(cursor.fetchall())

    print(f"Selected {len(pilot_chunks)} representative chunks for calibration pilot (MCA, RBI, SEBI).")

    pilot_results = []
    succeeded = 0
    failed = 0
    empty = 0

    t0 = time.time()
    for i, chunk_row in enumerate(pilot_chunks, 1):
        success, output = coordinator.process_chunk(chunk_row, run_id)
        if success:
            if output.extraction_status == "empty":
                empty += 1
            else:
                succeeded += 1
        else:
            failed += 1

        pilot_results.append({
            "chunk_id": output.chunk_id,
            "document_id": output.document_id,
            "status": output.extraction_status,
            "entities_count": len(output.entities),
            "relationships_count": len(output.relationships),
            "unresolved_count": len(output.unresolved_references),
            "warnings": output.warnings,
        })

        if i % 25 == 0 or i == len(pilot_chunks):
            print(f"[{i}/{len(pilot_chunks)}] Processed. Succeeded: {succeeded}, Empty: {empty}, Failed: {failed}")

    elapsed = time.time() - t0
    print(f"Pilot execution finished in {elapsed:.2f}s.")

    # Save pilot evaluation report
    PILOT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = PILOT_DIR / "pilot_results.json"
    report = {
        "run_id": run_id,
        "model": MODEL_NAME,
        "execution_route": EXECUTION_ROUTE,
        "total_pilot_chunks": len(pilot_chunks),
        "succeeded": succeeded,
        "empty": empty,
        "failed": failed,
        "results": pilot_results,
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"Pilot results saved to: {report_path}")


def _process_single_chunk_worker(row_dict: dict) -> Tuple[bool, ChunkExtractionOutput, str]:
    file_path = row_dict["file_path"]
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as fp:
            raw_text = fp.read()
    except Exception as e:
        output = ChunkExtractionOutput(
            schema_version="1.0.0",
            chunk_id=row_dict["chunk_id"],
            document_id=row_dict["document_id"],
            extraction_status="failed",
            warnings=[f"File read error: {e}"]
        )
        return False, output, ""

    output = SemanticLegalExtractor.extract_chunk(row_dict, raw_text)
    val_result, validated_output = ExtractionValidator.validate_and_align_offsets(output, raw_text)
    cache_key = compute_cache_key(raw_text)
    return val_result.is_valid, validated_output, cache_key


def cmd_extract(args):
    from concurrent.futures import ThreadPoolExecutor

    store = ExtractionStore(DB_PATH)
    run_id = f"extract_run_{int(time.time())}"
    store.create_run(run_id, notes=f"Parallel batch extraction pass")

    batch_size = args.batch_size
    target_all = (batch_size <= 0)

    print(f"=== Running Parallel Extraction Pipeline (batch_size={'ALL' if target_all else batch_size}) ===")

    total_processed = 0
    total_succeeded = 0
    total_empty = 0
    total_failed = 0
    t_start = time.time()

    CHUNK_FETCH_SIZE = 5000

    while True:
        limit = CHUNK_FETCH_SIZE if target_all else min(CHUNK_FETCH_SIZE, batch_size - total_processed)
        if limit <= 0:
            break

        pending_chunks = store.get_pending_chunks(limit=limit, regulator=args.regulator)
        if not pending_chunks:
            print("No more pending chunks in queue.")
            break

        rows_data = [dict(r) for r in pending_chunks]
        t_batch = time.time()

        # Parallel extraction & validation
        with ThreadPoolExecutor(max_workers=16) as pool:
            worker_results = list(pool.map(_process_single_chunk_worker, rows_data))

        # Separate valid outputs from failures
        valid_items = []
        for is_valid, output, cache_key in worker_results:
            if is_valid:
                valid_items.append((output, cache_key))
                if output.extraction_status == "empty":
                    total_empty += 1
                else:
                    total_succeeded += 1
            else:
                total_failed += 1
                store.record_failure(output.chunk_id, run_id, "VALIDATION_FAILED", "; ".join(output.warnings))

        # Bulk commit to SQLite
        store.save_chunk_extractions_batch(valid_items, run_id)

        count_in_batch = len(rows_data)
        total_processed += count_in_batch
        elapsed_batch = time.time() - t_batch
        rate = count_in_batch / max(elapsed_batch, 0.001)

        print(
            f"Processed {total_processed} chunks | Batch rate: {rate:.0f} chunks/s | "
            f"Succeeded: {total_succeeded}, Empty: {total_empty}, Failed: {total_failed}",
            flush=True
        )

        if not target_all and total_processed >= batch_size:
            break

    elapsed_total = time.time() - t_start
    print(f"\nExtraction pass completed: {total_processed} chunks in {elapsed_total:.2f}s ({total_processed / max(elapsed_total, 0.001):.0f} chunks/s).")
    cmd_status(args)


def cmd_resolve(args):
    print("=== Running Conservative Entity Resolution ===")
    store = ExtractionStore(DB_PATH)
    resolver = ConservativeEntityResolver(store)
    result = resolver.resolve_entities()
    print(f"Resolution Summary: {result}")


def cmd_export(args):
    print("=== Generating GraphRAG / FalkorDB Exports ===")
    store = ExtractionStore(DB_PATH)
    exporter = GraphRAGExporter(store, EXPORTS_DIR)
    counts = exporter.export_all()
    print(f"Exports written to {EXPORTS_DIR}:")
    for k, v in counts.items():
        print(f"  - {k}: {v}")


def cmd_verify(args):
    print("=== Running Corpus Verification ===")
    store = ExtractionStore(DB_PATH)
    verifier = CorpusVerifier(store)
    results = verifier.run_all_checks()
    for name, res in results["details"].items():
        status_str = "PASS" if res.get("passed") else "WARN/FAIL"
        print(f"[{status_str}] {res.get('check', name)}: {res}")


def cmd_status(args):
    store = ExtractionStore(DB_PATH)
    counts = store.get_manifest_counts()
    print("\n=== Corpus Processing Status ===")
    print(f"Total Chunks In Manifest : {counts.get('total', 0)}")
    print(f"  - Succeeded Extractions: {counts.get('succeeded', 0)}")
    print(f"  - Empty / Boilerplate  : {counts.get('empty', 0)}")
    print(f"  - Pending Chunks       : {counts.get('pending', 0)}")
    print(f"  - Needs Context        : {counts.get('needs_context', 0)}")
    print(f"  - Validation Failed    : {counts.get('validation_failed', 0)}")
    print(f"  - Execution Failed     : {counts.get('failed', 0)}")
    print("================================\n")


def main():
    parser = argparse.ArgumentParser(description="Regulatory Knowledge Graph Extraction CLI")
    subparsers = parser.add_subparsers(dest="command")

    # manifest
    p_man = subparsers.add_parser("manifest", help="Build manifest and sync to DB")
    p_man.add_argument("--max-chunks", type=int, default=None, help="Limit chunk scan for testing")

    # pilot
    p_pilot = subparsers.add_parser("pilot", help="Run calibration pilot")

    # extract
    p_ext = subparsers.add_parser("extract", help="Run batch extraction")
    p_ext.add_argument("--batch-size", type=int, default=100, help="Number of chunks to extract")
    p_ext.add_argument("--regulator", type=str, default=None, help="Filter by regulator (MCA, RBI, SEBI)")

    # resolve
    subparsers.add_parser("resolve", help="Run conservative entity resolution")

    # export
    subparsers.add_parser("export", help="Export to JSONL for FalkorDB")

    # verify
    subparsers.add_parser("verify", help="Run integrity and completeness verification")

    # status
    subparsers.add_parser("status", help="Show processing progress and queue counts")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    dispatch = {
        "manifest": cmd_manifest,
        "pilot": cmd_pilot,
        "extract": cmd_extract,
        "resolve": cmd_resolve,
        "export": cmd_export,
        "verify": cmd_verify,
        "status": cmd_status,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
