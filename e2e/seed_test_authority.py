"""Task 12 E2E seed: ONE clearly-labeled TEST authority point in server Qdrant.

This is NOT legal content and NEVER claims to be: source E2E-TEST-LIBRARY,
article TEST-ARTICLE-1. It proves the library-first plumbing (zero-upload
query -> authority citation with version+edition) while task 3 awaits real
curated legal source files. Uses the app's own QdrantStore + CrispEmbed
client so schema and vectors are production-path.

Usage: uv run python e2e/seed_test_authority.py  (needs :6333 + :8080 live)
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "backend")

from app.library.embedder import CrispEmbedClient  # noqa: E402
from app.search.store import QdrantStore  # noqa: E402

TEST_POINT = {
    "id": "e2e-test-annual-leave",
    "source": "E2E-TEST-LIBRARY",
    "version": "2099-test",
    "edition": "ar-general",
    "pub_date": "2099-01-01",
    "doc_date": None,
    "language": "ar",
    "article_or_section": "TEST-ARTICLE-1",
    "text": "TEST FIXTURE (not law): the annual leave period for the synthetic "
    "test worker is thirty days. This sentence exists only to prove the "
    "library-first retrieval plumbing carries version and edition.",
}


async def main() -> None:
    embedder = CrispEmbedClient(base_url="http://localhost:8080", model="bge-m3")
    vectors = await embedder.embed([TEST_POINT["text"]])
    assert len(vectors[0]) == 1024, f"unexpected dim {len(vectors[0])}"
    store = QdrantStore(url="http://localhost:6333")
    count = store.upsert_authorities([{**TEST_POINT, "vector": vectors[0]}])
    print(f"seeded {count} TEST authority point(s): {TEST_POINT['article_or_section']}")


if __name__ == "__main__":
    asyncio.run(main())
