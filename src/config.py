"""Configuration and schema definitions for the Regulatory Knowledge Graph Extraction Engine.
Indian Regulatory Corpus (MCA, RBI, SEBI).
"""

import os
from pathlib import Path

# Base Paths
ROOT_DIR = Path(__file__).resolve().parent.parent
HPC_DIR = ROOT_DIR / "hpc_results"
CHUNKS_DIR = HPC_DIR / "chunks_docling_hybrid"
PARSED_DIR = HPC_DIR / "parsed_docling"

DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "extraction_store.db"
MANIFEST_PATH = DATA_DIR / "manifest.json"
EXPORTS_DIR = DATA_DIR / "exports"
FEW_SHOTS_DIR = DATA_DIR / "few_shots"
PILOT_DIR = DATA_DIR / "pilot"

# Model and Engine Metadata
MODEL_NAME = "Gemini-3.8-Flash-High"
EXECUTION_ROUTE = "Antigravity Execution Environment"
SCHEMA_VERSION = "1.0.0"
PROMPT_VERSION = "1.0.0"

# Schema Constants
ENTITY_TYPES = [
    "Authority",
    "Instrument",
    "Provision",
    "EntityCategory",
    "Organization",
    "Role",
    "Requirement",
    "DefinedTerm",
]

RELATIONSHIP_TYPES = [
    "ISSUES",
    "HAS_PROVISION",
    "DEFINES",
    "REFERS_TO",
    "APPLIES_TO",
    "REQUIRES",
    "PROHIBITS",
    "PERMITS",
    "EXEMPTS_FROM",
    "AMENDS",
    "SUPERSEDES",
]

# Permitted Endpoint Types (Source -> Relationship -> Target)
PERMITTED_RELATIONSHIP_ENDPOINTS = {
    "ISSUES": {
        "sources": {"Authority"},
        "targets": {"Instrument"},
    },
    "HAS_PROVISION": {
        "sources": {"Instrument", "Provision"},
        "targets": {"Provision"},
    },
    "DEFINES": {
        "sources": {"Provision", "Instrument"},
        "targets": {"DefinedTerm"},
    },
    "REFERS_TO": {
        "sources": {"Provision", "Instrument", "Requirement", "Authority"},
        "targets": {"Provision", "Instrument", "Authority", "DefinedTerm"},
    },
    "APPLIES_TO": {
        "sources": {"Instrument", "Provision", "Requirement"},
        "targets": {"EntityCategory", "Organization", "Role", "DefinedTerm", "Authority", "Provision", "Instrument"},
    },
    "REQUIRES": {
        "sources": {"Provision", "Instrument", "Authority", "EntityCategory", "Role"},
        "targets": {"Requirement"},
    },
    "PROHIBITS": {
        "sources": {"Provision", "Instrument", "Authority", "EntityCategory", "Role"},
        "targets": {"Requirement"},
    },
    "PERMITS": {
        "sources": {"Provision", "Instrument", "Authority", "EntityCategory", "Role"},
        "targets": {"Requirement"},
    },
    "EXEMPTS_FROM": {
        "sources": {"Provision", "Instrument", "Authority"},
        "targets": {"EntityCategory", "Organization", "Provision", "Requirement"},
    },
    "AMENDS": {
        "sources": {"Instrument", "Provision"},
        "targets": {"Instrument", "Provision"},
    },
    "SUPERSEDES": {
        "sources": {"Instrument", "Provision"},
        "targets": {"Instrument", "Provision"},
    },
}

# Modality Categories
MODALITIES = [
    "obligation",     # shall, must, is required to
    "prohibition",    # shall not, must not, cannot
    "permission",     # may, is permitted to, is entitled to
    "recommendation", # should, ought to
]

# Lifecycle Statuses
PROCESSING_STATUSES = [
    "pending",
    "running",
    "succeeded",
    "needs_context",
    "validation_failed",
    "failed",
    "empty",
]

REVIEW_STATUSES = [
    "unreviewed",
    "approved",
    "rejected",
    "needs_review",
]

# Regulator Standard Names
REGULATOR_NAMES = {
    "MCA": "Ministry of Corporate Affairs, Government of India",
    "RBI": "Reserve Bank of India",
    "SEBI": "Securities and Exchange Board of India",
}

# FalkorDB Graph Settings
def resolve_falkordb_host() -> str:
    env_h = os.getenv("FALKORDB_HOST")
    if env_h:
        return env_h
    import socket
    for candidate in ["127.0.0.1", "localhost"]:
        try:
            s = socket.socket()
            s.settimeout(0.3)
            s.connect((candidate, 6379))
            s.close()
            return candidate
        except Exception:
            pass
    import subprocess
    try:
        out = subprocess.check_output(["wsl", "hostname", "-I"], text=True, timeout=5)
        return out.strip().split()[0]
    except Exception:
        return "127.0.0.1"

FALKORDB_HOST = resolve_falkordb_host()
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", "6379"))
FALKORDB_GRAPH_NAME = os.getenv("FALKORDB_GRAPH_NAME", "legal_kg_dev")
FALKORDB_BATCH_SIZE = int(os.getenv("FALKORDB_BATCH_SIZE", "1000"))

def get_falkordb_client():
    from falkordb import FalkorDB
    return FalkorDB(
        host=resolve_falkordb_host(),
        port=FALKORDB_PORT,
        socket_keepalive=True,
        socket_timeout=120,
        socket_connect_timeout=10,
    )

