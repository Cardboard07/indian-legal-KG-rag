import sys
import json
import openpyxl

sys.stdout.reconfigure(encoding='utf-8')

wb = openpyxl.load_workbook('RBI, SEBI and MCA questions .xlsx', read_only=True)
ws = wb['RBI & SEBI Questions']
rows = list(ws.iter_rows(values_only=True))

header = [str(h).strip() if h is not None else f'col_{i}' for i, h in enumerate(rows[0])]
target_ids = {3, 4, 5, 6, 7, 9, 14, 63}

questions = []
for r in rows[1:]:
    sno = r[0]
    try:
        sno_int = int(float(sno)) if sno is not None else None
    except Exception:
        sno_int = None
    if sno_int in target_ids:
        item = {'id': sno_int}
        for col_name, val in zip(header, r):
            if val is not None and not col_name.startswith('col_'):
                item[col_name] = str(val).strip()
        questions.append(item)

# Sort by ID
questions.sort(key=lambda x: x['id'])

with open('data/pilot_questions.json', 'w', encoding='utf-8') as f:
    json.dump(questions, f, indent=2, ensure_ascii=False)

print(f"Successfully exported {len(questions)} pilot questions to data/pilot_questions.json:")
for q in questions:
    print(f"  [Q{q['id']}] {q.get('Compliance Question', '')[:100]}...")
