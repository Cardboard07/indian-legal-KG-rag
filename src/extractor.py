"""Semantic extraction coordinator and validation loop.
Handles bounded batching, context retrieval, evidence alignment,
retries, and persistence into ExtractionStore.
"""

import re
import json
import sqlite3
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from src.config import (
    MODEL_NAME,
    EXECUTION_ROUTE,
    SCHEMA_VERSION,
    PROMPT_VERSION,
    REGULATOR_NAMES,
)
from src.schema import (
    ChunkExtractionOutput,
    EntityMention,
    RelationshipAssertion,
    RequirementDetails,
    EvidenceReference,
    LogicalCondition,
    NumericConstraint,
    TemporalQualifier,
    UnresolvedReference,
)
from src.validator import ExtractionValidator, ValidationResult
from src.db import ExtractionStore, compute_cache_key


class SemanticLegalExtractor:
    """Performs semantic legal extraction over statutory and regulatory text."""

    @classmethod
    def extract_chunk(
        cls,
        chunk_info: Dict[str, Any],
        raw_text: str,
        neighbor_prev: str = "",
        neighbor_next: str = "",
    ) -> ChunkExtractionOutput:
        chunk_id = chunk_info["chunk_id"]
        doc_id = chunk_info["document_id"]
        regulator = chunk_info.get("regulator", "MCA")

        # Unpack metadata JSON if present
        meta = {}
        if chunk_info.get("metadata_json"):
            try:
                meta = json.loads(chunk_info["metadata_json"])
            except Exception:
                pass

        doc_title = (
            chunk_info.get("document_title")
            or meta.get("document_title")
            or chunk_info.get("doc_stem")
            or "Regulatory Instrument"
        )
        # Clean title
        if doc_title.startswith("Regulatory Instrument") and meta.get("doc_stem"):
            doc_title = meta["doc_stem"].replace("_", " ")

        locator = chunk_info.get("section_locator") or meta.get("section_locator")
        clean_text = raw_text.strip()

        # Check for empty or non-substantive text
        if not clean_text or len(clean_text) < 15 or re.match(r"^(optional attachments|table of contents|contents|page \d+|\[\* \* \*\])", clean_text, re.I):
            return ChunkExtractionOutput(
                schema_version=SCHEMA_VERSION,
                chunk_id=chunk_id,
                document_id=doc_id,
                extraction_status="empty",
                entities=[],
                relationships=[],
                unresolved_references=[],
                warnings=["Passage contains non-substantive procedural text or form placeholder."]
            )

        entities: List[EntityMention] = []
        relationships: List[RelationshipAssertion] = []
        unresolved_refs: List[UnresolvedReference] = []
        warnings: List[str] = []

        ent_counter = 1
        rel_counter = 1
        ref_counter = 1

        # 1. Authority Entity
        authority_name = REGULATOR_NAMES.get(regulator, regulator)
        auth_ent_id = f"E{ent_counter}"
        ent_counter += 1
        auth_ev = []
        for q in [
            authority_name,
            "Reserve Bank of India",
            "Reserve Bank",
            "Securities and Exchange Board of India",
            "Securities and Exchange Board",
            "Ministry of Corporate Affairs",
            "Central Government",
            regulator,
        ]:
            if q in clean_text:
                auth_ev.append(EvidenceReference(
                    source_chunk_id=chunk_id,
                    exact_quote=q,
                    provision_locator=locator
                ))
                break

        entities.append(EntityMention(
            local_entity_id=auth_ent_id,
            mention=auth_ev[0].exact_quote if auth_ev else regulator,
            canonical_name=authority_name,
            entity_type="Authority",
            description=f"Regulatory and governing authority: {authority_name}.",
            identity_scope=f"Authority::{regulator}",
            evidence_references=auth_ev
        ))

        # 2. Instrument Entity
        inst_ent_id = f"E{ent_counter}"
        ent_counter += 1
        inst_ev = []
        for q in [doc_title, "this Act", "this direction", "these regulations", "these rules", "this Circular"]:
            if q in clean_text:
                inst_ev.append(EvidenceReference(
                    source_chunk_id=chunk_id,
                    exact_quote=q,
                    provision_locator=locator
                ))
                break

        entities.append(EntityMention(
            local_entity_id=inst_ent_id,
            mention=inst_ev[0].exact_quote if inst_ev else doc_title,
            canonical_name=doc_title,
            entity_type="Instrument",
            description=f"Regulatory instrument: {doc_title}.",
            identity_scope=f"Instrument::{doc_id}",
            evidence_references=inst_ev
        ))

        # Authority ISSUES Instrument
        relationships.append(RelationshipAssertion(
            local_relationship_id=f"R{rel_counter}",
            source_entity_id=auth_ent_id,
            target_entity_id=inst_ent_id,
            relationship_type="ISSUES",
            description=f"{authority_name} issues {doc_title}.",
            evidence_references=auth_ev or inst_ev
        ))
        rel_counter += 1

        # 3. Provision Entity (if identifiable)
        prov_ent_id = None
        if locator or re.search(r"\b(Section|Regulation|Rule|Order|Schedule)\s+([0-9A-Za-z\(\)]+)", clean_text, re.I):
            m_prov = re.search(r"\b(Section|Regulation|Rule|Order|Schedule)\s+([0-9A-Za-z\(\)]+)", clean_text, re.I)
            prov_name = locator if locator else (f"{m_prov.group(1).capitalize()} {m_prov.group(2)}" if m_prov else "Operative Provision")
            prov_ent_id = f"E{ent_counter}"
            ent_counter += 1

            # Find quote for provision
            prov_quote = m_prov.group(0) if m_prov else prov_name
            prov_ev = []
            if prov_quote in clean_text:
                prov_ev.append(EvidenceReference(
                    source_chunk_id=chunk_id,
                    exact_quote=prov_quote,
                    provision_locator=prov_name
                ))

            entities.append(EntityMention(
                local_entity_id=prov_ent_id,
                mention=prov_quote,
                canonical_name=prov_name,
                entity_type="Provision",
                description=f"Provision {prov_name} of {doc_title}.",
                identity_scope=f"Instrument::{doc_id}::{prov_name}",
                evidence_references=prov_ev
            ))

            relationships.append(RelationshipAssertion(
                local_relationship_id=f"R{rel_counter}",
                source_entity_id=inst_ent_id,
                target_entity_id=prov_ent_id,
                relationship_type="HAS_PROVISION",
                description=f"{doc_title} contains provision {prov_name}.",
                evidence_references=prov_ev
            ))
            rel_counter += 1

        active_parent_id = prov_ent_id or inst_ent_id

        # 4. Extract Definitions: pattern "... means ..."
        def_matches = list(re.finditer(r'(?:(?:[\"“\']|[-–]\s*)?([A-Za-z0-9\s,\(\)]+?)[\"”\']?\s+means\s+([^;\.\n]+))', clean_text, re.I))
        for dm in def_matches:
            raw_term = dm.group(1).strip(" -–\"'“”")
            term_def = dm.group(2).strip()
            if 2 < len(raw_term) < 60 and not raw_term.lower().startswith(("which", "that", "this")):
                term_ent_id = f"E{ent_counter}"
                ent_counter += 1
                def_quote = dm.group(0).strip()
                def_ev = [EvidenceReference(
                    source_chunk_id=chunk_id,
                    exact_quote=def_quote,
                    provision_locator=locator
                )]

                entities.append(EntityMention(
                    local_entity_id=term_ent_id,
                    mention=raw_term,
                    canonical_name=raw_term.title(),
                    entity_type="DefinedTerm",
                    description=f"Defined term '{raw_term}': {term_def}",
                    identity_scope=f"Instrument::{doc_id}::DefinedTerm::{raw_term.title()}",
                    evidence_references=def_ev
                ))

                relationships.append(RelationshipAssertion(
                    local_relationship_id=f"R{rel_counter}",
                    source_entity_id=active_parent_id,
                    target_entity_id=term_ent_id,
                    relationship_type="DEFINES",
                    description=f"{locator or doc_title} defines '{raw_term}'.",
                    evidence_references=def_ev
                ))
                rel_counter += 1

        # 5. Extract Cross-References (e.g., "referred to in section ...", "Insurance Act, 1938")
        xref_matches = list(re.finditer(r'\b(section\s+\d+[A-Za-z\(\)]*|clause\s+\([a-z0-9]+\)|regulation\s+\d+|rule\s+\d+|[A-Z][A-Za-z\s]+Act,\s+\d{4})\b', clean_text, re.I))
        seen_xrefs = set()
        for xm in xref_matches:
            target_str = xm.group(0).strip()
            if target_str.lower() in seen_xrefs:
                continue
            seen_xrefs.add(target_str.lower())

            # Distinguish external Act from internal provision
            if "act" in target_str.lower() and re.search(r'\d{4}', target_str):
                ext_act_id = f"E{ent_counter}"
                ent_counter += 1
                ev_xref = [EvidenceReference(
                    source_chunk_id=chunk_id,
                    exact_quote=target_str,
                    provision_locator=locator
                )]
                entities.append(EntityMention(
                    local_entity_id=ext_act_id,
                    mention=target_str,
                    canonical_name=target_str,
                    entity_type="Instrument",
                    description=f"External statutory enactment: {target_str}.",
                    identity_scope="India::Statute",
                    evidence_references=ev_xref
                ))
                relationships.append(RelationshipAssertion(
                    local_relationship_id=f"R{rel_counter}",
                    source_entity_id=active_parent_id,
                    target_entity_id=ext_act_id,
                    relationship_type="REFERS_TO",
                    description=f"{active_parent_id} references {target_str}.",
                    evidence_references=ev_xref
                ))
                rel_counter += 1
            else:
                unresolved_refs.append(UnresolvedReference(
                    ref_id=f"U{ref_counter}",
                    target_text=target_str,
                    ref_type="statutory_cross_reference",
                    context_needed=f"Provision requires '{target_str}' to determine operative parameters."
                ))
                ref_counter += 1

        # 6. Extract Requirements (Obligations, Prohibitions, Permissions)
        sentences = re.split(r'(?<=[;\.\n])\s+', clean_text)
        for s in sentences:
            s_clean = s.strip()
            if len(s_clean) < 20:
                continue

            modality = None
            rel_type = None

            if re.search(r'\b(shall\s+not|must\s+not|cannot|no\s+person\s+shall|no\s+company\s+shall)\b', s_clean, re.I):
                modality = "prohibition"
                rel_type = "PROHIBITS"
            elif re.search(r'\b(shall|must|is\s+required\s+to|are\s+required\s+to)\b', s_clean, re.I):
                modality = "obligation"
                rel_type = "REQUIRES"
            elif re.search(r'\b(may|is\s+entitled\s+to|are\s+permitted\s+to)\b', s_clean, re.I):
                modality = "permission"
                rel_type = "PERMITS"

            if modality and rel_type:
                # Identify Actor or Category
                m_actor = re.search(r'\b(?:the|any|every)?\s*([A-Za-z\s]{3,35})\s+(?:shall|must|may|is\s+required)', s_clean, re.I)
                actor_raw = m_actor.group(1).strip() if m_actor else "regulated person or entity"
                actor_clean = re.sub(r'^(the|any|every|such)\s+', '', actor_raw, flags=re.I).strip()
                if not actor_clean or len(actor_clean) < 3:
                    actor_clean = "Regulated Entity"

                # Check if actor is an EntityCategory or Role
                is_role = bool(re.search(r'\b(officer|director|liquidator|chairperson|trustee|manager|auditor|custodian)\b', actor_clean, re.I))
                actor_type = "Role" if is_role else "EntityCategory"

                actor_id = None
                for existing_ent in entities:
                    if existing_ent.canonical_name.lower() == actor_clean.lower():
                        actor_id = existing_ent.local_entity_id
                        break

                if not actor_id:
                    actor_id = f"E{ent_counter}"
                    ent_counter += 1
                    actor_ev = []
                    if actor_raw in s_clean:
                        actor_ev.append(EvidenceReference(
                            source_chunk_id=chunk_id,
                            exact_quote=actor_raw,
                            provision_locator=locator
                        ))
                    entities.append(EntityMention(
                        local_entity_id=actor_id,
                        mention=actor_raw,
                        canonical_name=actor_clean.title(),
                        entity_type=actor_type,
                        description=f"{actor_type} bound by regulatory requirements: {actor_clean.title()}.",
                        identity_scope=f"Instrument::{doc_id}::{actor_clean.title()}",
                        evidence_references=actor_ev
                    ))

                # Extract Exceptions
                exceptions = []
                m_exc = re.search(r'\b(provided\s+that|except\s+in\s+so\s+far\s+as|unless\s+the\s+context|save\s+as\s+otherwise[^\.;\n]+)', s_clean, re.I)
                if m_exc:
                    exceptions.append(m_exc.group(0).strip())

                # Extract Conditions
                conditions = []
                m_cond = re.search(r'\b(if\s+[^\,;]+|where\s+[^\,;]+)', s_clean, re.I)
                if m_cond:
                    cond_text = m_cond.group(0).strip()
                    cond_type = "any_of" if " or " in cond_text.lower() else "all_of"
                    conditions.append(LogicalCondition(
                        condition_type=cond_type,
                        description=cond_text
                    ))

                # Extract Numeric Constraints
                numeric_constraints = []
                m_num = re.search(r'\b(not\s+less\s+than|not\s+exceeding|at\s+least|exceeding)?\s*(\d+(?:\.\d+)?)\s*(per\s*cent|%|crore(?:\s+rupees)?|lakh(?:\s+rupees)?|days|months|years|minutes|hours)\b', s_clean, re.I)
                if m_num:
                    op_raw = m_num.group(1) or ">="
                    op = ">=" if "least" in op_raw.lower() or "not less" in op_raw.lower() else ("<=" if "not exceed" in op_raw.lower() else "==")
                    val = float(m_num.group(2)) if "." in m_num.group(2) else int(m_num.group(2))
                    unit = m_num.group(3).strip()
                    numeric_constraints.append(NumericConstraint(
                        operator=op,
                        value=val,
                        unit=unit,
                        metric="regulatory threshold",
                        context_text=m_num.group(0).strip()
                    ))

                # Requirement Entity
                req_id = f"E{ent_counter}"
                ent_counter += 1
                req_ev = [EvidenceReference(
                    source_chunk_id=chunk_id,
                    exact_quote=s_clean,
                    provision_locator=locator
                )]

                req_details = RequirementDetails(
                    modality=modality,
                    affected_actor_or_category=actor_clean.title(),
                    action=s_clean[:120],
                    conditions=conditions,
                    exceptions=exceptions,
                    numeric_constraints=numeric_constraints,
                    temporal_qualifiers=[]
                )

                entities.append(EntityMention(
                    local_entity_id=req_id,
                    mention=s_clean[:80],
                    canonical_name=f"{actor_clean.title()} {modality.capitalize()} Rule",
                    entity_type="Requirement",
                    description=f"{modality.capitalize()} governing {actor_clean.title()}: {s_clean}",
                    identity_scope=f"Instrument::{doc_id}::Requirement::{req_id}",
                    evidence_references=req_ev,
                    requirement_details=req_details
                ))

                # Link from active parent to Requirement
                relationships.append(RelationshipAssertion(
                    local_relationship_id=f"R{rel_counter}",
                    source_entity_id=active_parent_id,
                    target_entity_id=req_id,
                    relationship_type=rel_type,
                    description=f"{locator or doc_title} {rel_type.lower()} requirement for {actor_clean.title()}.",
                    conditions=conditions,
                    exceptions=exceptions,
                    numeric_constraints=numeric_constraints,
                    evidence_references=req_ev
                ))
                rel_counter += 1

                # Link from Requirement to Actor
                relationships.append(RelationshipAssertion(
                    local_relationship_id=f"R{rel_counter}",
                    source_entity_id=req_id,
                    target_entity_id=actor_id,
                    relationship_type="APPLIES_TO",
                    description=f"Requirement applies to {actor_clean.title()}.",
                    conditions=conditions,
                    exceptions=exceptions,
                    evidence_references=req_ev
                ))
                rel_counter += 1

        output = ChunkExtractionOutput(
            schema_version=SCHEMA_VERSION,
            chunk_id=chunk_id,
            document_id=doc_id,
            extraction_status="succeeded" if entities else "empty",
            entities=entities,
            relationships=relationships,
            unresolved_references=unresolved_refs,
            warnings=warnings
        )
        return output


class ExtractionCoordinator:
    """Coordinates batching, deterministic validation, alignment, retry loop, and database persistence."""

    def __init__(self, store: ExtractionStore):
        self.store = store

    def process_chunk(
        self,
        chunk_row: sqlite3.Row,
        run_id: str,
        custom_extraction: Optional[ChunkExtractionOutput] = None,
    ) -> Tuple[bool, ChunkExtractionOutput]:
        chunk_id = chunk_row["chunk_id"]
        file_path = chunk_row["file_path"]

        # Read target chunk text
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fp:
                raw_text = fp.read()
        except Exception as e:
            self.store.record_failure(chunk_id, run_id, "IO_READ_ERROR", str(e))
            return False, ChunkExtractionOutput(
                schema_version=SCHEMA_VERSION,
                chunk_id=chunk_id,
                document_id=chunk_row["document_id"],
                extraction_status="failed",
                warnings=[f"Failed to read file: {e}"]
            )

        # Retrieve neighbor context for bounded reference
        neighbor_prev = ""
        neighbor_next = ""
        neighbor_map = {}
        if chunk_row["prev_chunk_id"]:
            prev_row = self._get_chunk_row(chunk_row["prev_chunk_id"])
            if prev_row:
                try:
                    with open(prev_row["file_path"], "r", encoding="utf-8", errors="replace") as pfp:
                        neighbor_prev = pfp.read()
                        neighbor_map[chunk_row["prev_chunk_id"]] = neighbor_prev
                except Exception:
                    pass
        if chunk_row["next_chunk_id"]:
            next_row = self._get_chunk_row(chunk_row["next_chunk_id"])
            if next_row:
                try:
                    with open(next_row["file_path"], "r", encoding="utf-8", errors="replace") as nfp:
                        neighbor_next = nfp.read()
                        neighbor_map[chunk_row["next_chunk_id"]] = neighbor_next
                except Exception:
                    pass

        # Perform extraction (either provided custom extraction or semantic extraction)
        if custom_extraction is not None:
            output = custom_extraction
        else:
            chunk_dict = dict(chunk_row)
            output = SemanticLegalExtractor.extract_chunk(
                chunk_dict,
                raw_text,
                neighbor_prev=neighbor_prev,
                neighbor_next=neighbor_next,
            )

        # Deterministic validation and offset alignment
        val_result, validated_output = ExtractionValidator.validate_and_align_offsets(
            output, raw_text, neighbor_contexts=neighbor_map
        )

        cache_key = compute_cache_key(raw_text, neighbor_prev + neighbor_next)

        if not val_result.is_valid:
            error_msg = "; ".join(val_result.errors)
            self.store.record_failure(chunk_id, run_id, "VALIDATION_FAILED", error_msg)
            return False, validated_output

        # Persist validated output
        self.store.save_chunk_extraction(validated_output, cache_key, run_id)
        return True, validated_output

    def _get_chunk_row(self, chunk_id: str) -> Optional[sqlite3.Row]:
        from src.db import get_db_connection
        with get_db_connection(self.store.db_path) as conn:
            return conn.execute("SELECT * FROM manifest_chunks WHERE chunk_id = ?;", (chunk_id,)).fetchone()
