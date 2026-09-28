"""Task 17: coverage aggregation API - additive summary + capped lists.

All fixtures live in tmp dirs (monkeypatched MANIFEST/SEED/DB paths); these
tests never touch the live ledger or the derived JSON under backend/data/.
"""

import json
import sqlite3

import pytest
from httpx import ASGITransport, AsyncClient

import app.api.v1.library as libmod
import app.services.library_coverage as covmod
from app.services.library_coverage import coverage

LEDGER_SCHEMA = """
CREATE TABLE library_import_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT NOT NULL UNIQUE,
  sha256 TEXT NOT NULL,
  bytes INTEGER NOT NULL DEFAULT 0,
  pages INTEGER NOT NULL DEFAULT 0,
  category TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL,
  version TEXT NOT NULL,
  edition TEXT NOT NULL,
  parse_status TEXT NOT NULL DEFAULT 'pending',
  extract_status TEXT NOT NULL DEFAULT 'pending',
  embed_status TEXT NOT NULL DEFAULT 'pending',
  index_status TEXT NOT NULL DEFAULT 'pending',
  chunk_count INTEGER NOT NULL DEFAULT 0,
  indexed_count INTEGER NOT NULL DEFAULT 0
)
"""


def _make_ledger(db_path, rows):
    con = sqlite3.connect(str(db_path))
    con.executescript(LEDGER_SCHEMA)
    con.executemany(
        "INSERT INTO library_import_files "
        "(path, sha256, category, source, version, edition, "
        " parse_status, extract_status, embed_status, index_status, "
        " chunk_count, indexed_count) "
        "VALUES (?, 'abc', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.commit()
    con.close()


def _manifest_entry(source, version, edition="ar-general", category="cat-a"):
    return {
        "source": source,
        "version": version,
        "edition": edition,
        "file_path": f"{category}/{source}.pdf",
        "category": category,
    }


@pytest.fixture
def small_corpus(tmp_path, monkeypatch):
    """5-file ledger + matching derived JSON, all paths monkeypatched."""
    manifest = tmp_path / "library-manifest.json"
    seed = tmp_path / "library-seed-state.json"
    db = tmp_path / "matters.db"
    rows = [
        # (path, category, source, version, edition, parse, extract, embed, index, chunks, indexed)
        (
            "a/1.pdf",
            "cat-a",
            "Src A",
            "v1",
            "ar-general",
            "parsed",
            "extracted",
            "embedded",
            "indexed",
            10,
            10,
        ),
        (
            "a/2.pdf",
            "cat-a",
            "Src B",
            "v1",
            "ar-general",
            "parsed",
            "extracted",
            "pending",
            "pending",
            8,
            0,
        ),
        (
            "b/3.pdf",
            "cat-b",
            "Src C",
            "v2",
            "ar-general",
            "parsed",
            "pending",
            "pending",
            "pending",
            0,
            0,
        ),
        (
            "c/4.pdf",
            "cat-b",
            "Src D",
            "v1",
            "fr-translation",
            "parsed",
            "pending",
            "pending",
            "pending",
            0,
            0,
        ),
        (
            "d/5.pdf",
            "",
            "Src E",
            "v9",
            "",
            "quarantined",
            "pending",
            "pending",
            "pending",
            0,
            0,
        ),
    ]
    _make_ledger(db, rows)
    # Manifest holds only the parsed winners (mirrors bulk_catalog export rule).
    manifest.write_text(
        json.dumps(
            {"entries": [_manifest_entry(*r[2:6]) for r in rows[:4]]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    seed.write_text(
        json.dumps(
            {
                f"{r[2]}@{r[3]}#{r[4]}": {"chunks": r[9], "embedded": r[10]}
                for r in rows[:2]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(covmod, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(covmod, "SEED_STATE_PATH", seed)
    monkeypatch.setattr(libmod.settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    return tmp_path


async def test_summary_totals_match_ledger(small_corpus):
    cov = await coverage()
    totals = cov["summary"]["totals"]
    assert totals == {
        "files": 5,
        "indexed": 1,
        "extracted": 2,
        "embedded": 1,
        "chunks": 18,
        "chunks_indexed": 10,
    }


async def test_summary_breakdowns(small_corpus):
    summary = (await coverage())["summary"]
    assert summary["by_category"] == {"cat-a": 2, "cat-b": 2, "unparsed": 1}
    assert summary["by_edition"] == {
        "ar-general": 3,
        "fr-translation": 1,
        "unparsed": 1,
    }
    assert summary["by_status"]["index_status"] == {"indexed": 1, "pending": 4}
    assert summary["by_status"]["extract_status"] == {"extracted": 2, "pending": 3}


async def test_existing_keys_unchanged_and_capped(small_corpus):
    cov = await coverage()
    assert set(cov) == {
        "titles",
        "gaps",
        "library_version",
        "summary",
        "titles_truncated",
        "gaps_truncated",
    }
    assert len(cov["titles"]) == 4
    assert cov["titles_truncated"] == 0
    # Per-title shape byte-compatible with the pre-task-17 contract.
    for t in cov["titles"]:
        assert set(t) == {
            "source",
            "version",
            "edition",
            "pub_date",
            "doc_date",
            "hijri_date",
            "language",
            "coverage_note",
            "chunks",
            "status",
        }
    assert cov["library_version"] == libmod.settings.LIBRARY_VERSION
    # 4 manifest entries, 2 seeded -> 2 gaps, untruncated.
    assert len(cov["gaps"]) == 2
    assert cov["gaps_truncated"] == 0


async def test_lists_truncate_at_200(tmp_path, monkeypatch):
    manifest = tmp_path / "library-manifest.json"
    seed = tmp_path / "library-seed-state.json"
    entries = [_manifest_entry(f"Src {i}", "v1") for i in range(250)]
    manifest.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    seed.write_text(json.dumps({}), encoding="utf-8")
    db = tmp_path / "matters.db"
    _make_ledger(
        db,
        [
            (
                f"x/{i}.pdf",
                "cat-a",
                f"Src {i}",
                "v1",
                "ar-general",
                "parsed",
                "pending",
                "pending",
                "pending",
                0,
                0,
            )
            for i in range(250)
        ],
    )
    monkeypatch.setattr(covmod, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(covmod, "SEED_STATE_PATH", seed)
    monkeypatch.setattr(libmod.settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    cov = await coverage()
    assert len(cov["titles"]) == 200
    assert cov["titles_truncated"] == 50
    # Manifest order preserved: first 200, entry shape intact.
    assert cov["titles"][0]["source"] == "Src 0"
    assert cov["titles"][199]["source"] == "Src 199"
    assert len(cov["gaps"]) == 200
    assert cov["gaps_truncated"] == 50
    assert cov["summary"]["totals"]["files"] == 250


async def test_missing_ledger_returns_200_zeros_and_gap(tmp_path, monkeypatch):
    """Missing manifest+ledger: HTTP 200, zero totals, gap string, never 500."""
    from app.main import app

    monkeypatch.setattr(covmod, "MANIFEST_PATH", tmp_path / "no-manifest.json")
    monkeypatch.setattr(covmod, "SEED_STATE_PATH", tmp_path / "no-seed.json")
    monkeypatch.setattr(
        libmod.settings,
        "DATABASE_URL",
        f"sqlite+aiosqlite:///{tmp_path}/no-ledger.db",
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/library/coverage")
    assert resp.status_code == 200
    body = resp.json()
    assert body["titles"] == []
    assert all(v == 0 for v in body["summary"]["totals"].values())
    assert body["summary"]["by_category"] == {}
    assert body["summary"]["by_status"] == {}
    assert body["summary"]["by_edition"] == {}
    assert any("ledger" in g for g in body["gaps"])
    assert body["library_version"] is None


async def test_ledger_present_but_manifest_missing_keeps_real_summary(
    tmp_path,
    monkeypatch,
):
    """Manifest missing + ledger present: manifest gap kept, summary is real."""
    db = tmp_path / "matters.db"
    _make_ledger(
        db,
        [
            (
                "a/1.pdf",
                "cat-a",
                "Src A",
                "v1",
                "ar-general",
                "parsed",
                "extracted",
                "embedded",
                "indexed",
                4,
                4,
            ),
        ],
    )
    monkeypatch.setattr(covmod, "MANIFEST_PATH", tmp_path / "no-manifest.json")
    monkeypatch.setattr(covmod, "SEED_STATE_PATH", tmp_path / "no-seed.json")
    monkeypatch.setattr(libmod.settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    cov = await coverage()
    assert cov["titles"] == []
    assert "library manifest missing" in cov["gaps"]
    assert cov["summary"]["totals"]["indexed"] == 1
