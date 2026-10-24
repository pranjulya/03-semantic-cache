import os

# Runtime is offline: models load from the local HuggingFace cache only.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np
from sentence_transformers import SentenceTransformer

from scache.config import settings


class Embedder:
    def __init__(self, model_name: str | None = None):
        self.model_id = model_name or settings.embed_model  # stored on every cache entry as embedder_id
        self.model = SentenceTransformer(self.model_id)

    def encode(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        # Question vs question is symmetric: no bge query-instruction prefix.
        return self.model.encode(texts, batch_size=batch_size, normalize_embeddings=True)
