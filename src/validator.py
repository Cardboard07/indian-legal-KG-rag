"""Deterministic validation engine for regulatory entity & relationship extractions.
Validates exact quotations, character offsets, endpoint types, modality consistency,
condition preservation, and referential integrity.
"""

from typing import List, Tuple, Dict, Any, Optional
import re
from src.schema import ChunkExtractionOutput, EntityMention, RelationshipAssertion, EvidenceReference
from src.config import PERMITTED_RELATIONSHIP_ENDPOINTS, MODALITIES


class ValidationResult:
    def __init__(self, is_valid: bool, errors: List[str], warnings: List[str]):
        self.is_valid = is_valid
        self.errors = errors
        self.warnings = warnings

    def __repr__(self) -> str:
        return f"<ValidationResult is_valid={self.is_valid} errors={len(self.errors)} warnings={len(self.warnings)}>"


class ExtractionValidator:
    """Performs deterministic validation and evidence offset calculation on extraction outputs."""

    @classmethod
    def validate_and_align_offsets(
        cls,
        output: ChunkExtractionOutput,
        raw_chunk_text: str,
        neighbor_contexts: Optional[Dict[str, str]] = None,
    ) -> Tuple[ValidationResult, ChunkExtractionOutput]:
        errors: List[str] = []
        warnings: List[str] = []

        if neighbor_contexts is None:
            neighbor_contexts = {}

        # 1. Map of local entity IDs to EntityMention
        entity_map: Dict[str, EntityMention] = {}
        for entity in output.entities:
            if entity.local_entity_id in entity_map:
                errors.append(f"Duplicate local_entity_id '{entity.local_entity_id}'")
            entity_map[entity.local_entity_id] = entity

        # 2. Validate Evidence References and Align Character Offsets
        for entity in output.entities:
            for ev in entity.evidence_references:
                cls._validate_evidence_span(ev, raw_chunk_text, neighbor_contexts, errors, warnings)

            # Modality and Requirement Details consistency check
            if entity.entity_type == "Requirement" and entity.requirement_details:
                req = entity.requirement_details
                if req.modality not in MODALITIES:
                    errors.append(f"Entity '{entity.local_entity_id}' has invalid modality '{req.modality}'")

                # Negation check
                if re.search(r'\b(shall\s+not|must\s+not|cannot|no\s+person\s+shall|no\s+company\s+shall)\b', entity.description, re.I):
                    if req.modality != "prohibition":
                        warnings.append(
                            f"Entity '{entity.local_entity_id}' description suggests prohibition but modality is '{req.modality}'"
                        )

        # 3. Validate Relationships
        rel_ids = set()
        for rel in output.relationships:
            if rel.local_relationship_id in rel_ids:
                errors.append(f"Duplicate local_relationship_id '{rel.local_relationship_id}'")
            rel_ids.add(rel.local_relationship_id)

            # Check source and target existence
            if rel.source_entity_id not in entity_map:
                errors.append(
                    f"Relationship '{rel.local_relationship_id}' refers to nonexistent source_entity_id '{rel.source_entity_id}'"
                )
                continue
            if rel.target_entity_id not in entity_map:
                errors.append(
                    f"Relationship '{rel.local_relationship_id}' refers to nonexistent target_entity_id '{rel.target_entity_id}'"
                )
                continue

            source_entity = entity_map[rel.source_entity_id]
            target_entity = entity_map[rel.target_entity_id]

            # Validate endpoint types
            rule = PERMITTED_RELATIONSHIP_ENDPOINTS.get(rel.relationship_type)
            if rule:
                if source_entity.entity_type not in rule["sources"]:
                    errors.append(
                        f"Relationship '{rel.local_relationship_id}' ({rel.relationship_type}) has invalid source type "
                        f"'{source_entity.entity_type}'. Permitted: {rule['sources']}"
                    )
                if target_entity.entity_type not in rule["targets"]:
                    errors.append(
                        f"Relationship '{rel.local_relationship_id}' ({rel.relationship_type}) has invalid target type "
                        f"'{target_entity.entity_type}'. Permitted: {rule['targets']}"
                    )
            else:
                errors.append(f"Unknown relationship type '{rel.relationship_type}'")

            # Validate evidence spans on relationships
            for ev in rel.evidence_references:
                cls._validate_evidence_span(ev, raw_chunk_text, neighbor_contexts, errors, warnings)

        # 4. Check for lost exceptions / conditions if source contains flag words
        if re.search(r'\b(provided\s+that|except\s+in\s+so\s+far\s+as|unless\s+the\s+context|save\s+as\s+otherwise)\b', raw_chunk_text, re.I):
            has_exception = False
            for ent in output.entities:
                if ent.requirement_details and ent.requirement_details.exceptions:
                    has_exception = True
                    break
                if ent.requirement_details and ent.requirement_details.conditions:
                    has_exception = True
                    break
            for rel in output.relationships:
                if rel.exceptions or rel.conditions:
                    has_exception = True
                    break
            if not has_exception and output.extraction_status == "succeeded" and len(output.entities) > 0:
                warnings.append("Source text appears to contain an exception clause ('provided that' / 'except' / 'unless'), but no exceptions or conditions were recorded.")

        # 5. Empty Extraction Check
        if not output.entities and output.extraction_status == "succeeded":
            output.extraction_status = "empty"

        is_valid = len(errors) == 0
        return ValidationResult(is_valid=is_valid, errors=errors, warnings=warnings), output

    @classmethod
    def _validate_evidence_span(
        cls,
        ev: EvidenceReference,
        raw_chunk_text: str,
        neighbor_contexts: Dict[str, str],
        errors: List[str],
        warnings: List[str],
    ):
        quote = ev.exact_quote.strip()
        if not quote:
            warnings.append(f"Evidence reference on chunk {ev.source_chunk_id} has empty exact_quote")
            return

        target_text = raw_chunk_text
        if ev.source_chunk_id in neighbor_contexts:
            target_text = neighbor_contexts[ev.source_chunk_id]

        idx = target_text.find(quote)
        if idx != -1:
            ev.char_start = idx
            ev.char_end = idx + len(quote)
        else:
            # Case-insensitive / whitespace-normalized fallback
            normalized_target = re.sub(r'\s+', ' ', target_text)
            normalized_quote = re.sub(r'\s+', ' ', quote)
            if normalized_quote.lower() in normalized_target.lower():
                ev.char_start = 0
                ev.char_end = len(quote)
                warnings.append(f"Quote matched with whitespace/case normalization: '{quote[:40]}...'")
            else:
                errors.append(f"Evidence quote not found in source text: '{quote[:60]}...'")
