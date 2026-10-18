"""Real corpus-derived few-shot calibration examples.
All examples are sourced from actual Indian regulatory chunks (MCA, RBI, SEBI).
"""

import json
from pathlib import Path
from typing import List, Dict, Any
from src.config import FEW_SHOTS_DIR

FEW_SHOT_EXAMPLES: List[Dict[str, Any]] = [
    {
        "category": "straightforward_obligation",
        "chunk_id": "RBI/1403/Amendments ( Acts )/RBI_CCP_chunk139.md",
        "passage": "- (1) The authorised CCP shall designate a Compliance Officer.",
        "context": "Document: RBI Directions on Central Counterparties (CCPs). Provision: Sub-clause (1).",
        "difficult_feature": "Direct mandatory command with explicit actor, role, and mandatory modal ('shall').",
        "review_status": "unreviewed",
        "expected_extraction": {
            "schema_version": "1.0.0",
            "chunk_id": "RBI/1403/Amendments ( Acts )/RBI_CCP_chunk139.md",
            "document_id": "RBI::1403::Amendments ( Acts )::RBI_CCP",
            "extraction_status": "succeeded",
            "entities": [
                {
                    "local_entity_id": "E1",
                    "mention": "authorised CCP",
                    "canonical_name": "Authorised Central Counterparty",
                    "entity_type": "EntityCategory",
                    "description": "A clearing entity authorised by Reserve Bank of India to operate as a central counterparty.",
                    "identity_scope": "RBI::CCP",
                    "evidence_references": [
                        {
                            "source_chunk_id": "RBI/1403/Amendments ( Acts )/RBI_CCP_chunk139.md",
                            "exact_quote": "The authorised CCP",
                            "char_start": 6,
                            "char_end": 24
                        }
                    ]
                },
                {
                    "local_entity_id": "E2",
                    "mention": "Compliance Officer",
                    "canonical_name": "Compliance Officer",
                    "entity_type": "Role",
                    "description": "Designated corporate officer responsible for regulatory adherence in an authorised CCP.",
                    "identity_scope": "Authorised Central Counterparty",
                    "evidence_references": [
                        {
                            "source_chunk_id": "RBI/1403/Amendments ( Acts )/RBI_CCP_chunk139.md",
                            "exact_quote": "Compliance Officer",
                            "char_start": 41,
                            "char_end": 59
                        }
                    ]
                },
                {
                    "local_entity_id": "E3",
                    "mention": "shall designate a Compliance Officer",
                    "canonical_name": "Designate Compliance Officer Requirement",
                    "entity_type": "Requirement",
                    "description": "Mandatory obligation for an authorised CCP to designate a Compliance Officer.",
                    "identity_scope": "RBI::CCP::1(1)",
                    "requirement_details": {
                        "modality": "obligation",
                        "affected_actor_or_category": "Authorised Central Counterparty",
                        "action": "designate a Compliance Officer",
                        "object_or_recipient": "Compliance Officer",
                        "conditions": [],
                        "exceptions": [],
                        "numeric_constraints": [],
                        "temporal_qualifiers": []
                    },
                    "evidence_references": [
                        {
                            "source_chunk_id": "RBI/1403/Amendments ( Acts )/RBI_CCP_chunk139.md",
                            "exact_quote": "The authorised CCP shall designate a Compliance Officer.",
                            "char_start": 6,
                            "char_end": 60
                        }
                    ]
                }
            ],
            "relationships": [
                {
                    "local_relationship_id": "R1",
                    "source_entity_id": "E1",
                    "target_entity_id": "E3",
                    "relationship_type": "REQUIRES",
                    "description": "Authorised CCP is required to designate a Compliance Officer.",
                    "conditions": [],
                    "exceptions": [],
                    "numeric_constraints": [],
                    "temporal_qualifiers": [],
                    "evidence_references": [
                        {
                            "source_chunk_id": "RBI/1403/Amendments ( Acts )/RBI_CCP_chunk139.md",
                            "exact_quote": "The authorised CCP shall designate a Compliance Officer.",
                            "char_start": 6,
                            "char_end": 60
                        }
                    ]
                }
            ],
            "unresolved_references": [],
            "context_requirements": [],
            "warnings": []
        }
    },
    {
        "category": "multiple_conditions_or",
        "chunk_id": "MCA/185/Act _ Rules _ Regulations/Companies_Act_2013_chunk1247.md",
        "passage": "7. Section 7\nIf there is no such Chairperson, or if he is not present within fifteen minutes after the time appointed for holding the meeting, or is unwilling to act as Chairperson of the meeting, the directors present shall elect one of their members to be Chairperson of the meeting.",
        "context": "Document: Companies Act 2013. Provision: Section 7 of relevant Schedule / Regulations.",
        "difficult_feature": "Three alternative triggers (disjunction via 'or') with a 15-minute temporal window before mandatory election.",
        "review_status": "unreviewed",
        "expected_extraction": {
            "schema_version": "1.0.0",
            "chunk_id": "MCA/185/Act _ Rules _ Regulations/Companies_Act_2013_chunk1247.md",
            "document_id": "MCA::185::Act _ Rules _ Regulations::Companies_Act_2013",
            "extraction_status": "succeeded",
            "entities": [
                {
                    "local_entity_id": "E1",
                    "mention": "Section 7",
                    "canonical_name": "Section 7",
                    "entity_type": "Provision",
                    "description": "Statutory provision specifying appointment of meeting Chairperson in absence of appointed Chairperson.",
                    "identity_scope": "Instrument::Companies_Act_2013",
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/185/Act _ Rules _ Regulations/Companies_Act_2013_chunk1247.md",
                            "exact_quote": "Section 7",
                            "char_start": 3,
                            "char_end": 12
                        }
                    ]
                },
                {
                    "local_entity_id": "E2",
                    "mention": "directors present",
                    "canonical_name": "Directors Present",
                    "entity_type": "Role",
                    "description": "Directors in attendance at the company meeting.",
                    "identity_scope": "Instrument::Companies_Act_2013",
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/185/Act _ Rules _ Regulations/Companies_Act_2013_chunk1247.md",
                            "exact_quote": "the directors present",
                            "char_start": 194,
                            "char_end": 215
                        }
                    ]
                },
                {
                    "local_entity_id": "E3",
                    "mention": "shall elect one of their members to be Chairperson of the meeting",
                    "canonical_name": "Elect Meeting Chairperson Requirement",
                    "entity_type": "Requirement",
                    "description": "Directors present must elect one of their members as Chairperson upon absence or unwillingness of appointed Chairperson.",
                    "identity_scope": "Instrument::Companies_Act_2013::Section_7",
                    "requirement_details": {
                        "modality": "obligation",
                        "affected_actor_or_category": "Directors Present",
                        "action": "elect one of their members to be Chairperson of the meeting",
                        "conditions": [
                            {
                                "condition_type": "any_of",
                                "description": "If there is no such Chairperson, OR if he is not present within fifteen minutes after the time appointed for holding the meeting, OR is unwilling to act as Chairperson of the meeting"
                            }
                        ],
                        "exceptions": [],
                        "numeric_constraints": [
                            {
                                "operator": ">=",
                                "value": 15,
                                "unit": "minutes",
                                "metric": "absence duration after appointed meeting time",
                                "context_text": "within fifteen minutes after the time appointed"
                            }
                        ],
                        "temporal_qualifiers": [
                            {
                                "qualifier_type": "duration",
                                "value_text": "fifteen minutes",
                                "standardized_duration_seconds": 900
                            }
                        ]
                    },
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/185/Act _ Rules _ Regulations/Companies_Act_2013_chunk1247.md",
                            "exact_quote": "the directors present shall elect one of their members to be Chairperson of the meeting.",
                            "char_start": 194,
                            "char_end": 282
                        }
                    ]
                }
            ],
            "relationships": [
                {
                    "local_relationship_id": "R1",
                    "source_entity_id": "E1",
                    "target_entity_id": "E3",
                    "relationship_type": "REQUIRES",
                    "description": "Section 7 requires directors present to elect Chairperson under disjunctive conditions.",
                    "conditions": [
                        {
                            "condition_type": "any_of",
                            "description": "No Chairperson exists, or absent after 15 minutes, or unwilling to act"
                        }
                    ],
                    "exceptions": [],
                    "numeric_constraints": [],
                    "temporal_qualifiers": [],
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/185/Act _ Rules _ Regulations/Companies_Act_2013_chunk1247.md",
                            "exact_quote": "Section 7\nIf there is no such Chairperson",
                            "char_start": 3,
                            "char_end": 44
                        }
                    ]
                }
            ],
            "unresolved_references": [],
            "context_requirements": [],
            "warnings": []
        }
    },
    {
        "category": "exception_clause",
        "chunk_id": "MCA/1192/Act _ Rules _ Regulations/Companies_Act_2013_chunk1.md",
        "passage": "(4) The provisions of this Act shall apply to-\n  (a) companies incorporated under this Act or under any previous company law;\n  (b) insurance companies, except in so far as the said provisions are inconsistent with the provisions of the Insurance Act, 1938 (4 of 1938) or the Insurance Regulatory and Development Authority Act, 1999 (41 of 1999);",
        "context": "Document: Companies Act 2013. Provision: Section 1(4).",
        "difficult_feature": "Applicability rule qualified by an explicit external inconsistency exception ('except in so far as').",
        "review_status": "unreviewed",
        "expected_extraction": {
            "schema_version": "1.0.0",
            "chunk_id": "MCA/1192/Act _ Rules _ Regulations/Companies_Act_2013_chunk1.md",
            "document_id": "MCA::1192::Act _ Rules _ Regulations::Companies_Act_2013",
            "extraction_status": "succeeded",
            "entities": [
                {
                    "local_entity_id": "E1",
                    "mention": "this Act",
                    "canonical_name": "Companies Act, 2013",
                    "entity_type": "Instrument",
                    "description": "The Companies Act, 2013 governing corporate incorporation and regulation in India.",
                    "identity_scope": "India::Statute",
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/1192/Act _ Rules _ Regulations/Companies_Act_2013_chunk1.md",
                            "exact_quote": "The provisions of this Act shall apply to",
                            "char_start": 0,
                            "char_end": 41
                        }
                    ]
                },
                {
                    "local_entity_id": "E2",
                    "mention": "insurance companies",
                    "canonical_name": "Insurance Company",
                    "entity_type": "EntityCategory",
                    "description": "Companies engaged in insurance business governed by Insurance Act and IRDA Act.",
                    "identity_scope": "India::Financial_Services",
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/1192/Act _ Rules _ Regulations/Companies_Act_2013_chunk1.md",
                            "exact_quote": "insurance companies",
                            "char_start": 128,
                            "char_end": 147
                        }
                    ]
                },
                {
                    "local_entity_id": "E3",
                    "mention": "Insurance Act, 1938",
                    "canonical_name": "Insurance Act, 1938",
                    "entity_type": "Instrument",
                    "description": "Principal legislation regulating the insurance industry in India.",
                    "identity_scope": "India::Statute",
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/1192/Act _ Rules _ Regulations/Companies_Act_2013_chunk1.md",
                            "exact_quote": "Insurance Act, 1938",
                            "char_start": 226,
                            "char_end": 245
                        }
                    ]
                }
            ],
            "relationships": [
                {
                    "local_relationship_id": "R1",
                    "source_entity_id": "E1",
                    "target_entity_id": "E2",
                    "relationship_type": "APPLIES_TO",
                    "description": "Companies Act applies to insurance companies subject to statutory inconsistency exception.",
                    "conditions": [],
                    "exceptions": [
                        "except in so far as the said provisions are inconsistent with the provisions of the Insurance Act, 1938 (4 of 1938) or the Insurance Regulatory and Development Authority Act, 1999 (41 of 1999)"
                    ],
                    "numeric_constraints": [],
                    "temporal_qualifiers": [],
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/1192/Act _ Rules _ Regulations/Companies_Act_2013_chunk1.md",
                            "exact_quote": "insurance companies, except in so far as the said provisions are inconsistent with the provisions of the Insurance Act, 1938",
                            "char_start": 128,
                            "char_end": 245
                        }
                    ]
                },
                {
                    "local_relationship_id": "R2",
                    "source_entity_id": "E1",
                    "target_entity_id": "E3",
                    "relationship_type": "REFERS_TO",
                    "description": "Companies Act provisions refer to Insurance Act 1938 for inconsistency boundary.",
                    "conditions": [],
                    "exceptions": [],
                    "numeric_constraints": [],
                    "temporal_qualifiers": [],
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/1192/Act _ Rules _ Regulations/Companies_Act_2013_chunk1.md",
                            "exact_quote": "Insurance Act, 1938",
                            "char_start": 226,
                            "char_end": 245
                        }
                    ]
                }
            ],
            "unresolved_references": [],
            "context_requirements": [],
            "warnings": []
        }
    },
    {
        "category": "prohibition_negation",
        "chunk_id": "MCA/181/Act _ Rules _ Regulations/Companies_Act_2013_chunk956.md",
        "passage": "Section 333: Section 333\n(4) The Company Liquidator shall not be entitled to disclaim any property in any case where an application in writing has been made to him by any person interested in the property requiring him to decide whether he will or will not disclaim and the Company Liquidator has not",
        "context": "Document: Companies Act 2013. Provision: Section 333(4).",
        "difficult_feature": "Explicit negative entitlement ('shall not be entitled to') creating a statutory prohibition conditional upon external party action.",
        "review_status": "unreviewed",
        "expected_extraction": {
            "schema_version": "1.0.0",
            "chunk_id": "MCA/181/Act _ Rules _ Regulations/Companies_Act_2013_chunk956.md",
            "document_id": "MCA::181::Act _ Rules _ Regulations::Companies_Act_2013",
            "extraction_status": "succeeded",
            "entities": [
                {
                    "local_entity_id": "E1",
                    "mention": "Section 333",
                    "canonical_name": "Section 333(4)",
                    "entity_type": "Provision",
                    "description": "Statutory provision restricting disclaimer of property by Company Liquidator.",
                    "identity_scope": "Instrument::Companies_Act_2013",
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/181/Act _ Rules _ Regulations/Companies_Act_2013_chunk956.md",
                            "exact_quote": "Section 333",
                            "char_start": 0,
                            "char_end": 11
                        }
                    ]
                },
                {
                    "local_entity_id": "E2",
                    "mention": "Company Liquidator",
                    "canonical_name": "Company Liquidator",
                    "entity_type": "Role",
                    "description": "Official appointed to conduct company liquidation.",
                    "identity_scope": "Instrument::Companies_Act_2013",
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/181/Act _ Rules _ Regulations/Companies_Act_2013_chunk956.md",
                            "exact_quote": "The Company Liquidator",
                            "char_start": 29,
                            "char_end": 51
                        }
                    ]
                },
                {
                    "local_entity_id": "E3",
                    "mention": "shall not be entitled to disclaim any property",
                    "canonical_name": "Property Disclaimer Prohibition",
                    "entity_type": "Requirement",
                    "description": "Company Liquidator is prohibited from disclaiming property once a written application is made by an interested person.",
                    "identity_scope": "Instrument::Companies_Act_2013::Section_333(4)",
                    "requirement_details": {
                        "modality": "prohibition",
                        "affected_actor_or_category": "Company Liquidator",
                        "action": "disclaim any property",
                        "conditions": [
                            {
                                "condition_type": "all_of",
                                "description": "where an application in writing has been made to him by any person interested in the property requiring him to decide whether he will or will not disclaim"
                            }
                        ],
                        "exceptions": [],
                        "numeric_constraints": [],
                        "temporal_qualifiers": []
                    },
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/181/Act _ Rules _ Regulations/Companies_Act_2013_chunk956.md",
                            "exact_quote": "The Company Liquidator shall not be entitled to disclaim any property",
                            "char_start": 29,
                            "char_end": 98
                        }
                    ]
                }
            ],
            "relationships": [
                {
                    "local_relationship_id": "R1",
                    "source_entity_id": "E1",
                    "target_entity_id": "E3",
                    "relationship_type": "PROHIBITS",
                    "description": "Section 333(4) prohibits property disclaimer by Company Liquidator upon written notice.",
                    "conditions": [],
                    "exceptions": [],
                    "numeric_constraints": [],
                    "temporal_qualifiers": [],
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/181/Act _ Rules _ Regulations/Companies_Act_2013_chunk956.md",
                            "exact_quote": "Section 333\n(4) The Company Liquidator shall not be entitled to disclaim any property",
                            "char_start": 13,
                            "char_end": 98
                        }
                    ]
                }
            ],
            "unresolved_references": [],
            "context_requirements": [],
            "warnings": []
        }
    },
    {
        "category": "numeric_threshold",
        "chunk_id": "SEBI/1001/Act _ Rules _ Regulations/Securities_and_Exchange_Board_of_India_Merchant_Bankers_Regulations_1992_chunk94.md",
        "passage": "7. The capital adequacy requirement referred to in clause (d) of regulation 6 shall be a net worth of not less than five crore rupees.",
        "context": "Document: SEBI (Merchant Bankers) Regulations, 1992. Provision: Regulation 7.",
        "difficult_feature": "Quantitative minimum net worth threshold with exact operator ('>='), unit ('crore rupees'), and cross-reference to regulation 6(d).",
        "review_status": "unreviewed",
        "expected_extraction": {
            "schema_version": "1.0.0",
            "chunk_id": "SEBI/1001/Act _ Rules _ Regulations/Securities_and_Exchange_Board_of_India_Merchant_Bankers_Regulations_1992_chunk94.md",
            "document_id": "SEBI::1001::Act _ Rules _ Regulations::Securities_and_Exchange_Board_of_India_Merchant_Bankers_Regulations_1992",
            "extraction_status": "succeeded",
            "entities": [
                {
                    "local_entity_id": "E1",
                    "mention": "Regulation 7",
                    "canonical_name": "Regulation 7",
                    "entity_type": "Provision",
                    "description": "Capital adequacy regulation specifying net worth threshold for merchant bankers.",
                    "identity_scope": "Instrument::SEBI_Merchant_Bankers_Regulations_1992",
                    "evidence_references": [
                        {
                            "source_chunk_id": "SEBI/1001/Act _ Rules _ Regulations/Securities_and_Exchange_Board_of_India_Merchant_Bankers_Regulations_1992_chunk94.md",
                            "exact_quote": "7.",
                            "char_start": 0,
                            "char_end": 2
                        }
                    ]
                },
                {
                    "local_entity_id": "E2",
                    "mention": "clause (d) of regulation 6",
                    "canonical_name": "Regulation 6(d)",
                    "entity_type": "Provision",
                    "description": "Registration condition specifying capital adequacy eligibility.",
                    "identity_scope": "Instrument::SEBI_Merchant_Bankers_Regulations_1992",
                    "evidence_references": [
                        {
                            "source_chunk_id": "SEBI/1001/Act _ Rules _ Regulations/Securities_and_Exchange_Board_of_India_Merchant_Bankers_Regulations_1992_chunk94.md",
                            "exact_quote": "clause (d) of regulation 6",
                            "char_start": 51,
                            "char_end": 77
                        }
                    ]
                },
                {
                    "local_entity_id": "E3",
                    "mention": "net worth of not less than five crore rupees",
                    "canonical_name": "Merchant Banker Net Worth Requirement",
                    "entity_type": "Requirement",
                    "description": "Applicant must possess net worth not less than five crore rupees.",
                    "identity_scope": "Instrument::SEBI_Merchant_Bankers_Regulations_1992::Reg_7",
                    "requirement_details": {
                        "modality": "obligation",
                        "affected_actor_or_category": "Merchant Banker Applicant",
                        "action": "maintain net worth",
                        "conditions": [],
                        "exceptions": [],
                        "numeric_constraints": [
                            {
                                "operator": ">=",
                                "value": 5,
                                "unit": "crore rupees",
                                "metric": "net worth",
                                "context_text": "a net worth of not less than five crore rupees"
                            }
                        ],
                        "temporal_qualifiers": []
                    },
                    "evidence_references": [
                        {
                            "source_chunk_id": "SEBI/1001/Act _ Rules _ Regulations/Securities_and_Exchange_Board_of_India_Merchant_Bankers_Regulations_1992_chunk94.md",
                            "exact_quote": "shall be a net worth of not less than five crore rupees.",
                            "char_start": 78,
                            "char_end": 134
                        }
                    ]
                }
            ],
            "relationships": [
                {
                    "local_relationship_id": "R1",
                    "source_entity_id": "E1",
                    "target_entity_id": "E3",
                    "relationship_type": "REQUIRES",
                    "description": "Regulation 7 mandates capital adequacy net worth threshold.",
                    "conditions": [],
                    "exceptions": [],
                    "numeric_constraints": [],
                    "temporal_qualifiers": [],
                    "evidence_references": [
                        {
                            "source_chunk_id": "SEBI/1001/Act _ Rules _ Regulations/Securities_and_Exchange_Board_of_India_Merchant_Bankers_Regulations_1992_chunk94.md",
                            "exact_quote": "The capital adequacy requirement referred to in clause (d) of regulation 6 shall be a net worth of not less than five crore rupees.",
                            "char_start": 3,
                            "char_end": 134
                        }
                    ]
                },
                {
                    "local_relationship_id": "R2",
                    "source_entity_id": "E1",
                    "target_entity_id": "E2",
                    "relationship_type": "REFERS_TO",
                    "description": "Regulation 7 explicitly cross-references Regulation 6(d).",
                    "conditions": [],
                    "exceptions": [],
                    "numeric_constraints": [],
                    "temporal_qualifiers": [],
                    "evidence_references": [
                        {
                            "source_chunk_id": "SEBI/1001/Act _ Rules _ Regulations/Securities_and_Exchange_Board_of_India_Merchant_Bankers_Regulations_1992_chunk94.md",
                            "exact_quote": "clause (d) of regulation 6",
                            "char_start": 51,
                            "char_end": 77
                        }
                    ]
                }
            ],
            "unresolved_references": [],
            "context_requirements": [],
            "warnings": []
        }
    },
    {
        "category": "amendment_relationship",
        "chunk_id": "MCA/1192/Amendments ( Acts )/Companies_Amendment_Act_2019_chunk1.md",
        "passage": "2. In section 2 of the Companies Act, 2013 (hereinafter referred to as the principal Act), in clause (41),-- Amendment of section 2.",
        "context": "Document: Companies (Amendment) Act, 2019. Provision: Section 2.",
        "difficult_feature": "Structural amendment modifying principal Act's specific provision.",
        "review_status": "unreviewed",
        "expected_extraction": {
            "schema_version": "1.0.0",
            "chunk_id": "MCA/1192/Amendments ( Acts )/Companies_Amendment_Act_2019_chunk1.md",
            "document_id": "MCA::1192::Amendments ( Acts )::Companies_Amendment_Act_2019",
            "extraction_status": "succeeded",
            "entities": [
                {
                    "local_entity_id": "E1",
                    "mention": "Companies (Amendment) Act, 2019",
                    "canonical_name": "Companies (Amendment) Act, 2019",
                    "entity_type": "Instrument",
                    "description": "Statute amending provisions of the Companies Act 2013.",
                    "identity_scope": "India::Statute",
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/1192/Amendments ( Acts )/Companies_Amendment_Act_2019_chunk1.md",
                            "exact_quote": "Amendment of section 2.",
                            "char_start": 111,
                            "char_end": 134
                        }
                    ]
                },
                {
                    "local_entity_id": "E2",
                    "mention": "Companies Act, 2013",
                    "canonical_name": "Companies Act, 2013",
                    "entity_type": "Instrument",
                    "description": "Principal Companies Act.",
                    "identity_scope": "India::Statute",
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/1192/Amendments ( Acts )/Companies_Amendment_Act_2019_chunk1.md",
                            "exact_quote": "Companies Act, 2013",
                            "char_start": 23,
                            "char_end": 42
                        }
                    ]
                },
                {
                    "local_entity_id": "E3",
                    "mention": "section 2 ... clause (41)",
                    "canonical_name": "Section 2(41)",
                    "entity_type": "Provision",
                    "description": "Provision in principal Act defining financial year.",
                    "identity_scope": "Instrument::Companies_Act_2013",
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/1192/Amendments ( Acts )/Companies_Amendment_Act_2019_chunk1.md",
                            "exact_quote": "section 2",
                            "char_start": 6,
                            "char_end": 15
                        }
                    ]
                }
            ],
            "relationships": [
                {
                    "local_relationship_id": "R1",
                    "source_entity_id": "E1",
                    "target_entity_id": "E2",
                    "relationship_type": "AMENDS",
                    "description": "Companies (Amendment) Act 2019 amends Companies Act 2013.",
                    "conditions": [],
                    "exceptions": [],
                    "numeric_constraints": [],
                    "temporal_qualifiers": [],
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/1192/Amendments ( Acts )/Companies_Amendment_Act_2019_chunk1.md",
                            "exact_quote": "In section 2 of the Companies Act, 2013",
                            "char_start": 3,
                            "char_end": 42
                        }
                    ]
                },
                {
                    "local_relationship_id": "R2",
                    "source_entity_id": "E1",
                    "target_entity_id": "E3",
                    "relationship_type": "AMENDS",
                    "description": "Amending enactment modifies specific provision clause (41) of Section 2.",
                    "conditions": [],
                    "exceptions": [],
                    "numeric_constraints": [],
                    "temporal_qualifiers": [],
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/1192/Amendments ( Acts )/Companies_Amendment_Act_2019_chunk1.md",
                            "exact_quote": "In section 2 of the Companies Act, 2013 (hereinafter referred to as the principal Act), in clause (41)",
                            "char_start": 3,
                            "char_end": 105
                        }
                    ]
                }
            ],
            "unresolved_references": [],
            "context_requirements": [],
            "warnings": []
        }
    },
    {
        "category": "definition_unresolved_cross_ref",
        "chunk_id": "MCA/1192/Act _ Rules _ Regulations/Companies_Act_2013_chunk4.md",
        "passage": "(2) -accounting standards‖ means the standards of accounting or any addendum thereto for companies or class of companies referred to in section 133;",
        "context": "Document: Companies Act 2013. Provision: Section 2(2).",
        "difficult_feature": "Explicit statutory definition containing an internal cross-reference to section 133 needing external provision context.",
        "review_status": "unreviewed",
        "expected_extraction": {
            "schema_version": "1.0.0",
            "chunk_id": "MCA/1192/Act _ Rules _ Regulations/Companies_Act_2013_chunk4.md",
            "document_id": "MCA::1192::Act _ Rules _ Regulations::Companies_Act_2013",
            "extraction_status": "succeeded",
            "entities": [
                {
                    "local_entity_id": "E1",
                    "mention": "Section 2(2)",
                    "canonical_name": "Section 2(2)",
                    "entity_type": "Provision",
                    "description": "Statutory provision defining accounting standards under Companies Act.",
                    "identity_scope": "Instrument::Companies_Act_2013",
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/1192/Act _ Rules _ Regulations/Companies_Act_2013_chunk4.md",
                            "exact_quote": "(2)",
                            "char_start": 0,
                            "char_end": 3
                        }
                    ]
                },
                {
                    "local_entity_id": "E2",
                    "mention": "accounting standards",
                    "canonical_name": "Accounting Standards",
                    "entity_type": "DefinedTerm",
                    "description": "Standards of accounting or addendum thereto for companies referred to in section 133.",
                    "identity_scope": "Instrument::Companies_Act_2013",
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/1192/Act _ Rules _ Regulations/Companies_Act_2013_chunk4.md",
                            "exact_quote": "accounting standards",
                            "char_start": 5,
                            "char_end": 25
                        }
                    ]
                },
                {
                    "local_entity_id": "E3",
                    "mention": "companies or class of companies",
                    "canonical_name": "Company",
                    "entity_type": "EntityCategory",
                    "description": "Companies or specified classes of companies subject to accounting standards.",
                    "identity_scope": "Instrument::Companies_Act_2013",
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/1192/Act _ Rules _ Regulations/Companies_Act_2013_chunk4.md",
                            "exact_quote": "companies or class of companies",
                            "char_start": 88,
                            "char_end": 119
                        }
                    ]
                }
            ],
            "relationships": [
                {
                    "local_relationship_id": "R1",
                    "source_entity_id": "E1",
                    "target_entity_id": "E2",
                    "relationship_type": "DEFINES",
                    "description": "Section 2(2) defines the term accounting standards.",
                    "conditions": [],
                    "exceptions": [],
                    "numeric_constraints": [],
                    "temporal_qualifiers": [],
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/1192/Act _ Rules _ Regulations/Companies_Act_2013_chunk4.md",
                            "exact_quote": "(2) -accounting standards‖ means the standards of accounting",
                            "char_start": 0,
                            "char_end": 60
                        }
                    ]
                },
                {
                    "local_relationship_id": "R2",
                    "source_entity_id": "E2",
                    "target_entity_id": "E3",
                    "relationship_type": "APPLIES_TO",
                    "description": "Accounting standards apply to companies or class of companies.",
                    "conditions": [],
                    "exceptions": [],
                    "numeric_constraints": [],
                    "temporal_qualifiers": [],
                    "evidence_references": [
                        {
                            "source_chunk_id": "MCA/1192/Act _ Rules _ Regulations/Companies_Act_2013_chunk4.md",
                            "exact_quote": "for companies or class of companies",
                            "char_start": 84,
                            "char_end": 119
                        }
                    ]
                }
            ],
            "unresolved_references": [
                {
                    "ref_id": "U1",
                    "target_text": "section 133",
                    "ref_type": "statutory_cross_reference",
                    "context_needed": "Requires Section 133 of Companies Act 2013 to identify prescribed standards and recommending institute."
                }
            ],
            "context_requirements": [
                "Lookup Section 133 to resolve prescribed accounting body."
            ],
            "warnings": []
        }
    },
    {
        "category": "empty_no_substantive_relationship",
        "chunk_id": "MCA/509/Amendments ( Acts )/IEPFRules_19082019_chunk319.md",
        "passage": "2. Optional attachments, if any.",
        "context": "Document: IEPF Rules Amendment 2019. Form instruction snippet.",
        "difficult_feature": "Form label without legal rule, actor, definition, or relation.",
        "review_status": "unreviewed",
        "expected_extraction": {
            "schema_version": "1.0.0",
            "chunk_id": "MCA/509/Amendments ( Acts )/IEPFRules_19082019_chunk319.md",
            "document_id": "MCA::509::Amendments ( Acts )::IEPFRules_19082019",
            "extraction_status": "empty",
            "entities": [],
            "relationships": [],
            "unresolved_references": [],
            "context_requirements": [],
            "warnings": [
                "Passage contains only procedural form filing title; no extractable substantive entity or rule."
            ]
        }
    }
]


def save_few_shots_json(output_path: Path = FEW_SHOTS_DIR / "few_shot_examples.json"):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(FEW_SHOT_EXAMPLES, f, indent=2)
    print(f"Saved {len(FEW_SHOT_EXAMPLES)} few-shot calibration examples to {output_path}")


if __name__ == "__main__":
    save_few_shots_json()
