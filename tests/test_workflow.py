"""Integration and unit tests for the regulatory extraction engine.
Uses isolated synthetic fixtures to verify schema, validation, storage, and offset alignment.
"""

import unittest
import tempfile
import json
from pathlib import Path

from src.schema import (
    ChunkExtractionOutput,
    EntityMention,
    RelationshipAssertion,
    EvidenceReference,
    RequirementDetails,
    LogicalCondition,
    NumericConstraint,
)
from src.validator import ExtractionValidator
from src.db import ExtractionStore, compute_cache_key
from src.config import SCHEMA_VERSION


class TestRegulatoryWorkflow(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_store.db"
        self.store = ExtractionStore(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_schema_validity(self):
        output = ChunkExtractionOutput(
            schema_version=SCHEMA_VERSION,
            chunk_id="TEST/chunk1.md",
            document_id="TEST::doc1",
            extraction_status="succeeded",
            entities=[
                EntityMention(
                    local_entity_id="E1",
                    mention="Securities and Exchange Board of India",
                    canonical_name="Securities and Exchange Board of India",
                    entity_type="Authority",
                    description="Statutory market regulator.",
                    identity_scope="Authority::SEBI",
                )
            ],
            relationships=[],
        )
        self.assertEqual(output.chunk_id, "TEST/chunk1.md")
        self.assertEqual(len(output.entities), 1)

    def test_offset_alignment_and_quote_verification(self):
        sample_text = "The authorised CCP shall designate a Compliance Officer."
        output = ChunkExtractionOutput(
            schema_version=SCHEMA_VERSION,
            chunk_id="TEST/chunk2.md",
            document_id="TEST::doc2",
            extraction_status="succeeded",
            entities=[
                EntityMention(
                    local_entity_id="E1",
                    mention="Compliance Officer",
                    canonical_name="Compliance Officer",
                    entity_type="Role",
                    description="Compliance role.",
                    evidence_references=[
                        EvidenceReference(
                            source_chunk_id="TEST/chunk2.md",
                            exact_quote="Compliance Officer",
                            char_start=-1,
                            char_end=-1,
                        )
                    ],
                )
            ],
            relationships=[],
        )

        val_result, aligned_output = ExtractionValidator.validate_and_align_offsets(
            output, sample_text
        )
        self.assertTrue(val_result.is_valid, f"Validation failed: {val_result.errors}")
        ev = aligned_output.entities[0].evidence_references[0]
        self.assertEqual(ev.char_start, 37)
        self.assertEqual(ev.char_end, 55)
        self.assertEqual(sample_text[ev.char_start:ev.char_end], "Compliance Officer")

    def test_endpoint_validation(self):
        # Invalid relationship: Authority -> HAS_PROVISION -> Provision (HAS_PROVISION requires Instrument/Provision source)
        output = ChunkExtractionOutput(
            schema_version=SCHEMA_VERSION,
            chunk_id="TEST/chunk3.md",
            document_id="TEST::doc3",
            extraction_status="succeeded",
            entities=[
                EntityMention(
                    local_entity_id="E1",
                    mention="Reserve Bank of India",
                    canonical_name="Reserve Bank of India",
                    entity_type="Authority",
                    description="Regulator",
                ),
                EntityMention(
                    local_entity_id="E2",
                    mention="Section 45JA",
                    canonical_name="Section 45JA",
                    entity_type="Provision",
                    description="Statutory section",
                ),
            ],
            relationships=[
                RelationshipAssertion(
                    local_relationship_id="R1",
                    source_entity_id="E1",
                    target_entity_id="E2",
                    relationship_type="HAS_PROVISION",
                    description="Invalid endpoint test",
                )
            ],
        )

        val_result, _ = ExtractionValidator.validate_and_align_offsets(output, "Dummy text")
        self.assertFalse(val_result.is_valid)
        self.assertTrue(any("invalid source type 'Authority'" in err for err in val_result.errors))

    def test_empty_extraction_handling(self):
        output = ChunkExtractionOutput(
            schema_version=SCHEMA_VERSION,
            chunk_id="TEST/empty.md",
            document_id="TEST::doc_empty",
            extraction_status="succeeded",
            entities=[],
            relationships=[],
        )
        val_result, validated = ExtractionValidator.validate_and_align_offsets(output, "Optional attachments, if any.")
        self.assertTrue(val_result.is_valid)
        self.assertEqual(validated.extraction_status, "empty")

    def test_modality_negation_consistency(self):
        output = ChunkExtractionOutput(
            schema_version=SCHEMA_VERSION,
            chunk_id="TEST/prohib.md",
            document_id="TEST::doc_prohib",
            extraction_status="succeeded",
            entities=[
                EntityMention(
                    local_entity_id="E1",
                    mention="shall not disclose",
                    canonical_name="Non-disclosure Requirement",
                    entity_type="Requirement",
                    description="The officer shall not disclose confidential records.",
                    requirement_details=RequirementDetails(
                        modality="obligation",  # Contradicts "shall not"
                        affected_actor_or_category="Officer",
                        action="shall not disclose",
                    ),
                )
            ],
            relationships=[],
        )
        val_result, validated = ExtractionValidator.validate_and_align_offsets(
            output, "The officer shall not disclose confidential records."
        )
        self.assertTrue(val_result.is_valid)
        self.assertTrue(any("prohibition but modality is 'obligation'" in w for w in val_result.warnings))

    def test_cache_invalidation(self):
        k1 = compute_cache_key("content1", "ctx1", prompt_version="1.0.0")
        k2 = compute_cache_key("content1", "ctx1", prompt_version="1.0.1")
        k3 = compute_cache_key("content2", "ctx1", prompt_version="1.0.0")
        self.assertNotEqual(k1, k2)
        self.assertNotEqual(k1, k3)


if __name__ == "__main__":
    unittest.main()
