#!/usr/bin/env python3
"""
Phase 2 (step 1): downloads/verifies the embeddings model and checks the device.
Downloads to the HuggingFace cache the first time (~0.5 GB).

Usage:
    python embed/download_model.py
"""
from __future__ import annotations

import os

from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    model_name = os.environ.get("EMBED_MODEL", "FremyCompany/BioLORD-2023-M")
    device = os.environ.get("EMBED_DEVICE", "cpu")

    import torch  # noqa: WPS433
    from sentence_transformers import SentenceTransformer  # noqa: WPS433

    print(f"torch: {torch.__version__}")
    print(f"MPS available: {torch.backends.mps.is_available()}")
    print(f"Loading model: {model_name} (device={device})...")

    model = SentenceTransformer(model_name, device=device)
    dim = model.get_sentence_embedding_dimension()
    print(f"Embedding dimension: {dim}")
    assert dim == 768, f"Expected 768 dims, model gives {dim} -> adjust the vector() column."

    # Cross-lingual ES->EN test (should give high similarity):
    import numpy as np  # noqa: WPS433
    a, b = model.encode(["infarto agudo de miocardio", "acute myocardial infarction"],
                        normalize_embeddings=True)
    sim = float(np.dot(a, b))
    print(f"sim('infarto agudo de miocardio', 'acute myocardial infarction') = {sim:.3f}")
    print("OK." if sim > 0.6 else "WARNING: low similarity, check model/language.")


if __name__ == "__main__":
    main()
