"""TDD tests for todo 12 pure parts: OCR policy, record schema, zstd codec, eligibility.

No corpus access, no DB, no Tesseract: tmp dirs + small fixtures only.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.library.artifacts import (
    RECORD_FIELDS,
    artifact_path_for,
    build_record,
    read_artifact,
    write_artifact,
)
from app.library.bulk_extract import is_extract_eligible
from app.library.extract_worker import needs_ocr, split_pages_fallback


def test_needs_ocr_below_min_chars() -> None:
    assert needs_ocr("   \n  ", 20) is True
    assert needs_ocr("short", 20) is True
    assert needs_ocr("x" * 19, 20) is True


def test_needs_ocr_at_or_above_min_chars() -> None:
    assert needs_ocr("x" * 20, 20) is False
    assert needs_ocr("a" * 5000, 20) is False


def test_build_record_fixed_schema() -> None:
    rec = build_record(
        chunk_id="src:ver:ed:art:hash:p1:o0",
        text="body",
        page=3,
        hierarchy={"code": "X"},
        article="art",
        tokens=7,
        source="src",
        version="ver",
        edition="ed",
        category="cat",
        file_sha="deadbeef",
    )
    assert sorted(rec.keys()) == sorted(RECORD_FIELDS)
    assert rec["page"] == 3
    assert rec["tokens"] == 7
    assert rec["file_sha"] == "deadbeef"


def test_artifact_path_keyed_by_file_sha_stable(tmp_path: Path) -> None:
    first = artifact_path_for(tmp_path, "abc123")
    second = artifact_path_for(tmp_path, "abc123")
    assert first == second
    assert first.name == "abc123.jsonl.zst"
    assert artifact_path_for(tmp_path, "zzz").name == "zzz.jsonl.zst"


def test_codec_round_trip_streaming(tmp_path: Path) -> None:
    dest = tmp_path / "a.jsonl.zst"
    records = [
        build_record(
            chunk_id=f"id-{i}",
            text=f"text-{i}",
            page=i + 1,
            hierarchy={},
            article="art",
            tokens=i,
            source="s",
            version="v",
            edition="e",
            category="c",
            file_sha="sha",
        )
        for i in range(5)
    ]
    write_artifact(iter(records), dest)
    assert dest.exists() and dest.stat().st_size > 0
    back = list(read_artifact(dest))
    assert len(back) == 5
    assert [r["chunk_id"] for r in back] == [f"id-{i}" for i in range(5)]
    # Real zstd framing: the zstd CLI must decode it, not just our reader.
    import subprocess

    out = subprocess.run(
        ["zstd", "-dc", str(dest)], capture_output=True, check=True, timeout=30
    )
    assert len(out.stdout.decode("utf-8").strip().splitlines()) == 5
    first = json.loads(out.stdout.decode("utf-8").splitlines()[0])
    assert sorted(first.keys()) == sorted(RECORD_FIELDS)


def test_eligibility_parsed_is_eligible() -> None:
    eligible, _ = is_extract_eligible("parsed", None)
    assert eligible is True


def test_eligibility_name_quarantined_is_eligible_with_fallback() -> None:
    eligible, reason = is_extract_eligible("quarantined", "no detectable law type")
    assert eligible is True
    assert reason is None  # fallback provenance already in the ledger row


def test_eligibility_duplicate_is_skipped() -> None:
    eligible, reason = is_extract_eligible(
        "quarantined", "duplicate-of:some/winner.pdf"
    )
    assert eligible is False
    assert "duplicate-of" in (reason or "")


def test_eligibility_failed_and_pending_are_skipped() -> None:
    eligible, _ = is_extract_eligible("failed", "page-count: boom")
    assert eligible is False
    eligible, _ = is_extract_eligible("pending", None)
    assert eligible is False


def test_page_fallback_splits_overlong_page_with_linkage() -> None:
    from app.domain.authority.granite_tokens import MAX_GRANITE_TOKENS, count_granite_tokens

    long_page = " ".join(f"جملة رقم {i} من النص القانوني الطويل." for i in range(400))
    assert count_granite_tokens(long_page) > MAX_GRANITE_TOKENS
    chunks = split_pages_fallback([(3, long_page)], source_path="x/y.pdf")
    assert len(chunks) >= 2
    assert all((c.token_count or 0) <= MAX_GRANITE_TOKENS for c in chunks)
    assert all(c.page == 3 for c in chunks)
    assert chunks[0].continued_from is None
    assert all(c.continued_from is not None for c in chunks[1:])
    assert all(c.article_or_section for c in chunks)


def test_page_fallback_short_page_single_chunk_and_skips_empty() -> None:
    chunks = split_pages_fallback(
        [(1, "   "), (2, "المادة 1: نص قصير.")], source_path=None
    )
    assert len(chunks) == 1
    assert chunks[0].page == 2
    assert chunks[0].continued_from is None
