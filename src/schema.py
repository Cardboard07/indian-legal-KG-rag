"""Structured extraction schema definitions and validation models.
Compliant with Microsoft GraphRAG / FalkorDB extraction contract.
"""

from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field, field_validator, model_validator
from src.config import (
    ENTITY_TYPES,
    RELATIONSHIP_TYPES,
    PERMITTED_RELATIONSHIP_ENDPOINTS,
    MODALITIES,
    PROCESSING_STATUSES,
    SCHEMA_VERSION,
)


class EvidenceReference(BaseModel):
    """Verifiable quotation and offset link to source chunk."""
    source_chunk_id: str = Field(..., description="Chunk ID containing this exact quotation")
    exact_quote: str = Field(..., description="Verbatim text quotation from the source chunk")
    char_start: int = Field(default=-1, description="0-indexed start character offset in chunk content")
    char_end: int = Field(default=-1, description="0-indexed end character offset in chunk content")
    page_number: Optional[int] = Field(default=None, description="Physical page number if available")
    provision_locator: Optional[str] = Field(default=None, description="Section, rule, or paragraph locator")


class NumericConstraint(BaseModel):
    """Structured numeric threshold with unit, operator, and aggregation scope."""
    operator: str = Field(..., description="Comparison operator, e.g., '>=', '<=', '==', '>', '<', 'between'")
    value: Union[float, int, str] = Field(..., description="Numeric quantity or range")
    unit: str = Field(..., description="Unit of measurement, e.g., 'crore rupees', 'per cent', 'days', 'years'")
    metric: str = Field(..., description="Aggregation scope or metric, e.g., 'net worth', 'paid-up capital', 'turnover'")
    context_text: Optional[str] = Field(default=None, description="Original raw text phrasing")


class TemporalQualifier(BaseModel):
    """Temporal qualifier representing effective date, deadline, duration, or transition period."""
    qualifier_type: str = Field(..., description="'publication_date', 'effective_date', 'deadline', 'duration', 'transition_period'")
    value_text: str = Field(..., description="Raw text representation, e.g., 'within fifteen minutes', 'at once', '01-04-2020'")
    standardized_duration_seconds: Optional[int] = Field(default=None, description="Standardized duration in seconds if applicable")


class LogicalCondition(BaseModel):
    """Nested condition structure supporting all_of, any_of, not or raw condition clause."""
    condition_type: str = Field(default="raw", description="'all_of', 'any_of', 'not', 'raw'")
    description: str = Field(..., description="Readable description of condition")
    sub_conditions: Optional[List["LogicalCondition"]] = Field(default=None, description="Sub-conditions if nested")
    is_unresolved: bool = Field(default=False, description="True if condition interpretation is uncertain")


class RequirementDetails(BaseModel):
    """Specialized attributes preserved for Requirement entities."""
    modality: str = Field(..., description="'obligation', 'prohibition', 'permission', 'recommendation'")
    affected_actor_or_category: str = Field(..., description="Target actor, regulated entity category, or role bound by requirement")
    action: str = Field(..., description="Prescribed, prohibited, or permitted action/verb phrase")
    object_or_recipient: Optional[str] = Field(default=None, description="Object or recipient of the action")
    conditions: List[LogicalCondition] = Field(default_factory=list, description="Preserved conditions")
    exceptions: List[str] = Field(default_factory=list, description="Exceptions introduced by 'provided that', 'except', 'unless'")
    numeric_constraints: List[NumericConstraint] = Field(default_factory=list, description="Quantitative thresholds")
    temporal_qualifiers: List[TemporalQualifier] = Field(default_factory=list, description="Timing, deadlines, or durations")

    @field_validator("modality")
    def validate_modality(cls, v: str) -> str:
        if v not in MODALITIES:
            raise ValueError(f"Invalid modality '{v}'. Must be one of {MODALITIES}")
        return v


class EntityMention(BaseModel):
    """An extracted entity node."""
    local_entity_id: str = Field(..., description="Local ID within chunk, e.g., 'E1', 'E2'")
    mention: str = Field(..., description="Exact mention as found in text")
    canonical_name: str = Field(..., description="Proposed canonical name")
    entity_type: str = Field(..., description="Entity category type")
    description: str = Field(..., description="Substantive description preserving qualifications")
    identity_scope: Optional[str] = Field(default=None, description="Scope context, e.g., instrument ID for provisions or defining context")
    evidence_references: List[EvidenceReference] = Field(default_factory=list, description="Evidence spans supporting this entity")
    requirement_details: Optional[RequirementDetails] = Field(default=None, description="Required if entity_type is 'Requirement'")

    @field_validator("entity_type")
    def validate_entity_type(cls, v: str) -> str:
        if v not in ENTITY_TYPES:
            raise ValueError(f"Invalid entity_type '{v}'. Must be one of {ENTITY_TYPES}")
        return v

    @model_validator(mode="after")
    def validate_requirement_details_present(self):
        if self.entity_type == "Requirement" and self.requirement_details is None:
            # Fallback requirement details created from description
            self.requirement_details = RequirementDetails(
                modality="obligation",
                affected_actor_or_category="regulated entity",
                action=self.description,
            )
        return self


class RelationshipAssertion(BaseModel):
    """An extracted directional relationship between two local entities."""
    local_relationship_id: str = Field(..., description="Local ID, e.g., 'R1', 'R2'")
    source_entity_id: str = Field(..., description="Source local entity ID")
    target_entity_id: str = Field(..., description="Target local entity ID")
    relationship_type: str = Field(..., description="Relationship type")
    description: str = Field(..., description="Concise relationship description retaining qualifications")
    conditions: List[LogicalCondition] = Field(default_factory=list, description="Conditions qualifying relationship")
    exceptions: List[str] = Field(default_factory=list, description="Exceptions qualifying relationship")
    numeric_constraints: List[NumericConstraint] = Field(default_factory=list, description="Numeric thresholds")
    temporal_qualifiers: List[TemporalQualifier] = Field(default_factory=list, description="Temporal qualifiers")
    evidence_references: List[EvidenceReference] = Field(default_factory=list, description="Evidence spans supporting relationship")

    @field_validator("relationship_type")
    def validate_rel_type(cls, v: str) -> str:
        if v not in RELATIONSHIP_TYPES:
            raise ValueError(f"Invalid relationship_type '{v}'. Must be one of {RELATIONSHIP_TYPES}")
        return v


class UnresolvedReference(BaseModel):
    """An unresolved statutory cross-reference or pronoun reference needing external context."""
    ref_id: str = Field(..., description="Unique reference ID in chunk, e.g., 'U1'")
    target_text: str = Field(..., description="Exact reference text, e.g., 'section 133', 'the principal Act'")
    ref_type: str = Field(default="statutory_cross_reference", description="'statutory_cross_reference', 'delegated_legislation', 'external_act', 'ambiguous_pronoun'")
    context_needed: str = Field(..., description="Explanation of what text or provision is required")


class ChunkExtractionOutput(BaseModel):
    """The complete per-chunk extraction output contract."""
    schema_version: str = Field(default=SCHEMA_VERSION, description="Schema version")
    chunk_id: str = Field(..., description="Unique chunk identifier")
    document_id: str = Field(..., description="Parent document identifier")
    extraction_status: str = Field(default="succeeded", description="'succeeded', 'empty', 'needs_context', 'validation_failed', 'failed'")
    entities: List[EntityMention] = Field(default_factory=list, description="Extracted entities")
    relationships: List[RelationshipAssertion] = Field(default_factory=list, description="Extracted relationships")
    unresolved_references: List[UnresolvedReference] = Field(default_factory=list, description="Unresolved references")
    context_requirements: List[str] = Field(default_factory=list, description="Missing contextual requirements")
    warnings: List[str] = Field(default_factory=list, description="Processing warnings")

    @field_validator("extraction_status")
    def validate_status(cls, v: str) -> str:
        if v not in PROCESSING_STATUSES:
            raise ValueError(f"Invalid extraction_status '{v}'")
        return v
