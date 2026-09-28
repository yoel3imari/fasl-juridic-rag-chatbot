"""FastAPI dependency providers shared by the API routers.

`get_store()` and `get_embedder()` were duplicated verbatim in
`api/v1/chat.py` and `api/v1/search.py`. Both routers now import this
module and call through it (`deps.get_store()`), so there is exactly one
definition site and tests patch it as `app.api.deps` (aliased `deps_mod`).
"""

from __future__ import annotations

from app.services import search as svc


def get_store() -> svc.Store:
    """Factory seam: Qdrant store from settings (URL or local path)."""
    from app.infrastructure.qdrant.store import QdrantStore

    return QdrantStore()  # type: ignore[return-value]


def get_embedder() -> svc.Embedder:
    """Factory seam: CrispEmbed HTTP client (model from settings)."""
    from app.infrastructure.embeddings.client import CrispEmbedClient

    return CrispEmbedClient()  # type: ignore[return-value]
