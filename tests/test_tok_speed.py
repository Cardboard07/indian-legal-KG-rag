import time, re

sample = "The Reserve Bank of India hereby issues the Master Direction - Non-Banking Financial Company - Scale Based Regulation 2023. NBFC-BL entities with asset size under Rs 1,000 crore." * 20

t0 = time.time()
for _ in range(35000):
    tokens = re.findall(r"\b[a-zA-Z0-9_\-\.]{2,}\b", sample.lower())
t1 = time.time()

t2 = time.time()
for _ in range(35000):
    tokens2 = sample.lower().split()
t3 = time.time()

print(f"Regex findall took: {t1-t0:.2f}s | split() took: {t3-t2:.2f}s (Speedup: {(t1-t0)/(t3-t2):.1f}x)")
