import sqlite3
from src.extractor import SemanticLegalExtractor
from src.validator import ExtractionValidator
from collections import Counter

conn = sqlite3.connect(r'c:\Users\hrish\Projects\Anil_paper\Knowledge_graph\data\extraction_store.db')
conn.row_factory = sqlite3.Row
failed_chunks = conn.execute("SELECT c.* FROM manifest_chunks c JOIN chunk_processing_state s ON c.chunk_id = s.chunk_id WHERE s.processing_status = 'validation_failed';").fetchall()
conn.close()

err_counts = Counter()
for row in failed_chunks:
    with open(row['file_path'], 'r', encoding='utf-8', errors='replace') as fp:
        raw_txt = fp.read()
    output = SemanticLegalExtractor.extract_chunk(dict(row), raw_txt)
    val_res, val_out = ExtractionValidator.validate_and_align_offsets(output, raw_txt)
    for e in val_res.errors:
        # Simplify error message to category
        if "has invalid target type" in e:
            err_counts["invalid_target_type: " + e.split("has invalid target type")[-1].strip()] += 1
        elif "has invalid source type" in e:
            err_counts["invalid_source_type: " + e.split("has invalid source type")[-1].strip()] += 1
        else:
            err_counts[e] += 1

print('Error distribution across 40 failed chunks:')
for err, cnt in err_counts.most_common():
    print(f'  [{cnt}x] {err}')
