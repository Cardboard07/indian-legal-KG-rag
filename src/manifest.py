"""High-performance parallel corpus manifest scanner and metadata indexer.
Uses ThreadPoolExecutor for rapid traversal, SHA-256 calculation, and metadata extraction.
"""

import os
import re
import json
import hashlib
import time
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor
from src.config import CHUNKS_DIR, PARSED_DIR, MANIFEST_PATH, REGULATOR_NAMES


def compute_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def process_single_chunk(item: Tuple[str, str, str, str, str, int, str]) -> Dict[str, Any]:
    full_path, rel_path, doc_id, regulator, folder_id, category, chunk_idx = item

    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as fp:
            content = fp.read()
    except Exception:
        content = ""

    char_count = len(content)
    content_hash = compute_sha256(content)

    # Extract locators and headings
    locator = None
    headings = []
    for line in content.splitlines():
        line_s = line.strip()
        if line_s.startswith("#"):
            headings.append(line_s.lstrip("#").strip())
        if not locator:
            m_sec = re.match(r"^(?:(?:\d+\.)?\s*(Section|Regulation|Rule|Order|Schedule)\s+([0-9A-Za-z\(\)]+))", line_s, re.I)
            if m_sec:
                locator = f"{m_sec.group(1).capitalize()} {m_sec.group(2)}"
            else:
                m_num = re.match(r"^(\d+[A-Z]?)\.\s+([A-Za-z].*)", line_s)
                if m_num:
                    locator = f"Section/Clause {m_num.group(1)}"

    return {
        "chunk_id": rel_path,
        "document_id": doc_id,
        "file_path": full_path,
        "relative_path": rel_path,
        "regulator": regulator,
        "folder_id": folder_id,
        "category": category,
        "chunk_index": chunk_idx,
        "content_hash": content_hash,
        "char_count": char_count,
        "byte_count": len(content.encode("utf-8")),
        "section_locator": locator,
        "heading_hierarchy": headings,
    }


class ManifestBuilder:
    """Discovers, indexes, and extracts metadata for every chunk and document in the corpus."""

    @classmethod
    def build_manifest(cls, save_path: Optional[Path] = None, max_chunks: Optional[int] = None) -> Dict[str, Any]:
        if save_path is None:
            save_path = MANIFEST_PATH

        print(f"Scanning chunks directory: {CHUNKS_DIR}", flush=True)
        t0 = time.time()

        doc_chunks: Dict[str, List[Dict[str, Any]]] = {}
        items_to_process = []
        total_chunks = 0

        # Pass 1: Discover all markdown files and group by document
        for root, dirs, files in os.walk(CHUNKS_DIR):
            for f in files:
                if not f.endswith(".md"):
                    continue

                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, CHUNKS_DIR).replace("\\", "/")
                parts = rel_path.split("/")

                regulator = parts[0] if len(parts) > 0 else "UNKNOWN"
                folder_id = parts[1] if len(parts) > 1 else "unknown"
                category = parts[2] if len(parts) > 2 else "unknown"

                m = re.search(r"^(.*)_chunk(\d+)\.md$", f)
                if m:
                    doc_stem = m.group(1)
                    chunk_idx = int(m.group(2))
                else:
                    doc_stem = f[:-3]
                    chunk_idx = 0

                doc_id = f"{regulator}::{folder_id}::{category}::{doc_stem}"

                if doc_id not in doc_chunks:
                    doc_chunks[doc_id] = []

                doc_chunks[doc_id].append({
                    "file_path": full_path,
                    "rel_path": rel_path,
                    "filename": f,
                    "doc_stem": doc_stem,
                    "regulator": regulator,
                    "folder_id": folder_id,
                    "category": category,
                    "chunk_index": chunk_idx,
                })

                items_to_process.append((
                    full_path, rel_path, doc_id, regulator, folder_id, category, chunk_idx
                ))
                total_chunks += 1
                if max_chunks and total_chunks >= max_chunks:
                    break
            if max_chunks and total_chunks >= max_chunks:
                break

        print(f"Discovered {total_chunks} chunk files across {len(doc_chunks)} documents in {time.time() - t0:.2f}s.", flush=True)

        # Pass 2: Fast Parallel Chunk Reading & Hash Computation
        print("Reading chunk contents and computing hashes via thread pool...", flush=True)
        t1 = time.time()
        chunk_results_map: Dict[str, Dict[str, Any]] = {}
        empty_records = []

        with ThreadPoolExecutor(max_workers=16) as pool:
            for res in pool.map(process_single_chunk, items_to_process):
                chunk_results_map[res["chunk_id"]] = res
                if res["char_count"] == 0:
                    empty_records.append(res["chunk_id"])

        print(f"Processed {len(chunk_results_map)} chunks in {time.time() - t1:.2f}s.", flush=True)

        # Pass 3: Link Neighboring Chunks and Attach Document Metadata
        manifest_chunks: Dict[str, Dict[str, Any]] = {}
        documents_meta: Dict[str, Dict[str, Any]] = {}

        for doc_id, chunk_list in doc_chunks.items():
            sorted_chunks = sorted(chunk_list, key=lambda x: x["chunk_index"])
            first_chunk = sorted_chunks[0]

            doc_meta = cls._load_document_metadata(
                first_chunk["regulator"],
                first_chunk["folder_id"],
                first_chunk["category"],
                first_chunk["doc_stem"],
            )
            doc_meta["document_id"] = doc_id
            doc_meta["total_chunks"] = len(sorted_chunks)
            documents_meta[doc_id] = doc_meta

            for i, chunk_info in enumerate(sorted_chunks):
                cid = chunk_info["rel_path"]
                c_data = chunk_results_map[cid]

                c_data["prev_chunk_id"] = sorted_chunks[i - 1]["rel_path"] if i > 0 else None
                c_data["next_chunk_id"] = sorted_chunks[i + 1]["rel_path"] if i < len(sorted_chunks) - 1 else None
                c_data["document_title"] = doc_meta.get("document_title")
                c_data["enactment_date"] = doc_meta.get("enactment_date")
                c_data["act_number"] = doc_meta.get("act_number")

                manifest_chunks[cid] = c_data

        manifest = {
            "schema_version": "1.0.0",
            "total_documents": len(documents_meta),
            "total_chunks": len(manifest_chunks),
            "empty_records_count": len(empty_records),
            "unreadable_records_count": 0,
            "empty_records": empty_records,
            "unreadable_records": [],
            "documents": documents_meta,
            "chunks": manifest_chunks,
        }

        # Persist manifest file
        print(f"Serializing manifest JSON to {save_path}...", flush=True)
        os.makedirs(save_path.parent, exist_ok=True)
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f)

        print(f"Manifest build completed in {time.time() - t0:.2f}s.", flush=True)
        print(f"Total documents: {len(documents_meta)}, Total chunks: {len(manifest_chunks)}, Empty: {len(empty_records)}", flush=True)
        return manifest

    @classmethod
    def _load_document_metadata(cls, regulator: str, folder_id: str, category: str, doc_stem: str) -> Dict[str, Any]:
        """Reads document level title, act number, dates from parsed_docling if present."""
        structured_path = PARSED_DIR / regulator / folder_id / category / f"{doc_stem}_structured.json"

        meta = {
            "regulator": regulator,
            "regulator_authority": REGULATOR_NAMES.get(regulator, regulator),
            "folder_id": folder_id,
            "category": category,
            "doc_stem": doc_stem,
            "document_title": doc_stem.replace("_", " "),
            "act_number": None,
            "enactment_date": None,
            "source_filename": f"{doc_stem}.pdf",
            "document_version": "unknown",
            "url": None,
        }

        # Only read structured file if it exists and is < 3MB
        if structured_path.exists() and structured_path.stat().st_size < 3_000_000:
            try:
                with open(structured_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("document_title") and len(data["document_title"].strip()) > 3:
                        meta["document_title"] = data["document_title"].strip()
                    if data.get("act_number"):
                        meta["act_number"] = data["act_number"]
                    if data.get("enactment_date"):
                        meta["enactment_date"] = data["enactment_date"]
                    if "metadata" in data and isinstance(data["metadata"], dict):
                        meta.update(data["metadata"])
            except Exception:
                pass

        return meta
