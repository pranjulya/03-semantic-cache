"""One-time online step: fill the local HuggingFace cache. Everything else runs offline."""
from sentence_transformers import CrossEncoder, SentenceTransformer

from scache.config import settings

SentenceTransformer(settings.embed_model)
print("cached:", settings.embed_model)

# M5 comparison only (the chosen verifier is the local LLM judge): duplicate-question and STS cross-encoders.
for name in ("cross-encoder/quora-distilroberta-base", "cross-encoder/stsb-distilroberta-base"):
    CrossEncoder(name)
    print("cached:", name)
