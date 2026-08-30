"""Shared fixtures. These are integration tests: they need Postgres (with data + embeddings) and
the BioLORD model; the `llm`-marked ones also need the gemma endpoint. If the stack isn't up the
tests skip (not fail) with a hint, so `pytest` is safe to run anywhere."""
import pytest

from api.search import get_model, health


@pytest.fixture(scope="session", autouse=True)
def _stack_ready():
    """Skip the whole suite unless Postgres + embeddings are available; warm BioLORD once."""
    h = health()
    if not h["db"]["ok"]:
        pytest.skip("Postgres not reachable — run `make up`")
    if not h["db"].get("embeddings"):
        pytest.skip("No embeddings loaded — run `make embed`")
    get_model()  # load the embedding model a single time for the session
    return h


@pytest.fixture(scope="session")
def llm_ok():
    """True if the gemma endpoint is up; used to skip llm-marked tests gracefully."""
    return health()["llm"]["ok"]
