import numpy as np


class FakeEmbedder:
    """Maps known texts to fixed unit vectors. Unknown text raises, so tests state every vector they use."""

    model_id = "fake-embedder"

    def __init__(self, vectors: dict[str, list[float]]):
        self.vectors = {k: np.array(v, dtype=np.float32) / np.linalg.norm(v) for k, v in vectors.items()}
        self.calls = 0

    def encode(self, texts, batch_size=32):
        self.calls += len(texts)
        return np.stack([self.vectors[t] for t in texts])


def unit_at(angle_deg: float) -> list[float]:
    """2-D unit vector; cosine similarity between two of these is cos(angle difference)."""
    a = np.radians(angle_deg)
    return [float(np.cos(a)), float(np.sin(a)), 0.0]
