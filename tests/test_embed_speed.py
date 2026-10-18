import time
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
dummy_texts = ["This is a test regulatory chunk about NBFC capital adequacy and base layer requirements."] * 256

t0 = time.time()
embs = model.encode(dummy_texts, batch_size=64, normalize_embeddings=True)
elapsed = time.time() - t0
rate = len(dummy_texts) / elapsed
print(f"MiniLM encoded 256 chunks in {elapsed:.2f}s ({rate:.0f} chunks/sec)!")
