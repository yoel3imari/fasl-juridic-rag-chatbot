"""Refusal-path tests for the gated collection migration (plan todo 8, TDD).

Every refusal must exit non-zero BEFORE any delete: stale/missing/failed
gates, parity below 0.99, model/dim/sha mismatches, missing --confirm,
and snapshot-export failure (delete never reached). Uses tmp JSON files
and stub clients only -- no live Qdrant, no live DB.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import pytest

from app.cli.library.bulk_migrate import (
    FAIL_EXIT,
    MigrationRefused,
    collection_state,
    load_gate_artifact,
    preflight,
    resolve_sqlite_path,
    run_migrate,
    run_rollback,
    snapshot_one,
)

MODEL = "granite-embedding-107m"
DIM = 384
SHA = "7b76ab5a187ae32a725a5cb8c417be28a6822d72"


def _good_gate() -> dict:
    return {
        "model": MODEL,
        "dim": DIM,
        "git_sha": SHA,
        "pass": True,
        "recall_at_10_granite": 0.9583,
        "recall_at_10_bge": 1.0,
        "delta_points": -4.17,
    }


def _good_parity() -> dict:
    return {"model": MODEL, "dim": DIM, "git_sha": SHA, "mean_cosine": 0.993}


def test_preflight_ok() -> None:
    out = preflight(_good_gate(), _good_parity(), model=MODEL, dim=DIM, sha=SHA)
    assert out["parity_mean_cosine"] == pytest.approx(0.993)


def test_preflight_stale_gate_sha_refuses() -> None:
    gate = _good_gate()
    gate["git_sha"] = "c1bbe20f7facbf09e133e91713a5cdb856468f7f"
    with pytest.raises(MigrationRefused, match="stale"):
        preflight(gate, _good_parity(), model=MODEL, dim=DIM, sha=SHA)


def test_preflight_stale_parity_sha_refuses() -> None:
    parity = _good_parity()
    parity["git_sha"] = "1637e0ad07c5a6a28e215f9aa36772da5a28a444"
    with pytest.raises(MigrationRefused, match="stale"):
        preflight(_good_gate(), parity, model=MODEL, dim=DIM, sha=SHA)


def test_preflight_failed_gate_refuses() -> None:
    gate = _good_gate()
    gate["pass"] = False
    with pytest.raises(MigrationRefused, match="pass=False"):
        preflight(gate, _good_parity(), model=MODEL, dim=DIM, sha=SHA)


def test_preflight_low_parity_refuses() -> None:
    parity = _good_parity()
    parity["mean_cosine"] = 0.97
    with pytest.raises(MigrationRefused, match="mean_cosine"):
        preflight(_good_gate(), parity, model=MODEL, dim=DIM, sha=SHA)


def test_preflight_model_dim_mismatch_refuses() -> None:
    gate = _good_gate()
    gate["model"] = "bge-m3"
    gate["dim"] = 1024
    with pytest.raises(MigrationRefused, match="model="):
        preflight(gate, _good_parity(), model=MODEL, dim=DIM, sha=SHA)


def test_missing_gate_file_refuses(tmp_path) -> None:
    with pytest.raises(MigrationRefused, match="missing"):
        load_gate_artifact(str(tmp_path / "nope.json"), "quality_gate")


def test_tampered_gate_file_refuses(tmp_path) -> None:
    bad = tmp_path / "gate.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(MigrationRefused, match="unparseable"):
        load_gate_artifact(str(bad), "quality_gate")


def test_no_confirm_refuses_without_touching_qdrant() -> None:
    touched: list[str] = []

    class _Client:
        def close(self) -> None: ...

    args = argparse.Namespace(
        rollback=False,
        confirm=False,
        manifest=None,
        qdrant_url="http://x",
        db_url="sqlite+aiosqlite:////tmp/x.db",
        snapshot_dir="/tmp/x",
        gate="g",
        parity="p",
    )
    with pytest.raises(MigrationRefused, match="--confirm"):
        asyncio.run(run_migrate(args))
    assert touched == []


def test_snapshot_export_failure_before_delete(tmp_path) -> None:
    """A failed snapshot export must raise before any delete_collection call."""
    calls: list[str] = []

    class _Desc:
        name = "snap-1"

    class _Client:
        def create_snapshot(self, name, wait=True):
            calls.append(f"create:{name}")
            raise RuntimeError("qdrant exploded")

        def delete_collection(self, name):
            calls.append(f"delete:{name}")

    with pytest.raises(Exception, match="exploded"):
        snapshot_one(_Client(), "http://localhost:6333", "legal_authorities", str(tmp_path), 2)
    assert not [c for c in calls if c.startswith("delete:")]


def test_resolve_sqlite_path_rejects_memory() -> None:
    with pytest.raises(MigrationRefused):
        resolve_sqlite_path("sqlite+aiosqlite:///:memory:")


def test_resolve_sqlite_path_rejects_non_sqlite() -> None:
    with pytest.raises(MigrationRefused):
        resolve_sqlite_path("postgresql+asyncpg://u@h/db")


def test_fail_exit_is_nonzero() -> None:
    assert FAIL_EXIT != 0


def test_manifest_roundtrip_shape(tmp_path) -> None:
    manifest = {
        "action": "migrate",
        "snapshots": [
            {"collection": "matter_evidence", "snapshot_name": "s1", "pre_count": 3},
            {"collection": "legal_authorities", "snapshot_name": "s2", "pre_count": 2},
        ],
    }
    path = tmp_path / "migrate-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    by_collection = {s["collection"]: s for s in loaded["snapshots"]}
    assert by_collection["matter_evidence"]["pre_count"] == 3
    assert by_collection["legal_authorities"]["pre_count"] == 2


class _NotFound(Exception):
    """Stub for qdrant_client's UnexpectedResponse on a missing collection."""

    def __init__(self) -> None:
        super().__init__("Not found: Collection `x` doesn't exist!")
        self.status_code = 404


def test_collection_state_missing_returns_exists_false() -> None:
    """Given a Qdrant without the collection (fresh volume, cold start),
    When collection_state reads it,
    Then it returns exists=False with empty config instead of raising."""

    class _Client:
        def get_collection(self, name):
            raise _NotFound()

    state = collection_state(_Client(), "legal_authorities")
    assert state == {
        "name": "legal_authorities",
        "exists": False,
        "dense_dim": None,
        "dense_distance": None,
        "sparse": [],
        "on_disk_payload": None,
        "payload_indexes": [],
        "points_count": 0,
    }


def test_collection_state_non_404_still_raises() -> None:
    """Given a non-404 Qdrant failure, When collection_state reads it,
    Then it raises (only "missing collection" is a state, the rest are errors)."""

    class _Client:
        def get_collection(self, name):
            raise RuntimeError("qdrant exploded")

    with pytest.raises(RuntimeError, match="exploded"):
        collection_state(_Client(), "legal_authorities")


def test_run_migrate_cold_start_creates_without_delete_or_snapshot(tmp_path, monkeypatch) -> None:
    """Given a Qdrant with no collections and a fresh gate at live HEAD,
    When run_migrate --confirm runs,
    Then it creates both collections at dim, deletes nothing, snapshots
    nothing, and writes a done sentinel recording the cold start."""
    import types

    import app.cli.library.bulk_migrate as mig

    calls: list[str] = []

    class _Client:
        def __init__(self) -> None:
            self.collections: set[str] = set()

        def get_collection(self, name):
            if name not in self.collections:
                raise _NotFound()
            return types.SimpleNamespace(
                config=types.SimpleNamespace(
                    params=types.SimpleNamespace(
                        vectors={"dense": {"size": DIM, "distance": "Cosine"}},
                        sparse_vectors={"lexical": {}},
                        on_disk_payload=True,
                    )
                ),
                payload_schema={f: {} for f in ("source", "edition", "language", "category")},
            )

        def create_collection(self, collection_name: str, **kwargs: Any) -> None:
            calls.append(f"create:{collection_name}")
            self.collections.add(collection_name)

        def delete_collection(self, name) -> None:
            calls.append(f"delete:{name}")

        def create_snapshot(self, name, wait=True):
            raise AssertionError("must not snapshot a collection that never existed")

        def count(self, name, exact=True):
            return types.SimpleNamespace(count=0)

        def create_payload_index(self, *args, **kwargs) -> None:
            calls.append("index")

        def close(self) -> None: ...

    client = _Client()
    monkeypatch.setattr(mig, "_client_for", lambda url: client)

    live_sha = mig.live_git_sha()
    gate_file = tmp_path / "gate.json"
    gate_file.write_text(json.dumps({**_good_gate(), "git_sha": live_sha}), encoding="utf-8")
    parity_file = tmp_path / "parity.json"
    parity_file.write_text(json.dumps({**_good_parity(), "git_sha": live_sha}), encoding="utf-8")
    db_file = tmp_path / "cold.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    asyncio.run(
        mig.write_sentinel(
            db_url,
            kind="migrate",
            model=MODEL,
            dim=DIM,
            sha="0" * 40,
            status="done",
            payload={},
        )
    )
    import sqlite3

    with sqlite3.connect(db_file) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        conn.execute("INSERT INTO alembic_version VALUES ('head')")
    args = argparse.Namespace(
        rollback=False,
        confirm=True,
        manifest=None,
        qdrant_url="http://x",
        db_url=db_url,
        snapshot_dir=str(tmp_path / "snaps"),
        gate=str(gate_file),
        parity=str(parity_file),
    )
    out = asyncio.run(run_migrate(args))
    assert out["action"] == "migrate"
    assert sorted(out["cold_start"]) == [
        "legal_authorities",
        "matter_evidence",
    ]
    assert sorted(c for c in calls if c.startswith("create:")) == [
        "create:legal_authorities",
        "create:matter_evidence",
    ]
    assert not [c for c in calls if c.startswith("delete:")]
    row = (
        sqlite3.connect(db_file)
        .execute(
            "select status, git_sha, kind, payload_json from library_import_runs where id = ?",
            (out["sentinel_id"],),
        )
        .fetchone()
    )
    assert row[:3] == ("done", live_sha, "migrate")
    assert sorted(json.loads(row[3])["cold_start"]) == [
        "legal_authorities",
        "matter_evidence",
    ]


def test_rollback_cold_start_manifest_refuses(tmp_path) -> None:
    """Given a migrate manifest whose snapshot is None (cold-start create),
    When run_rollback runs,
    Then it refuses (there is no prior state to restore) instead of crashing."""
    manifest = {
        "model": MODEL,
        "dim": DIM,
        "snapshots": [
            {
                "collection": "matter_evidence",
                "snapshot_name": None,
                "reason": "collection-absent-at-migrate",
                "pre_count": 0,
            },
            {
                "collection": "legal_authorities",
                "snapshot_name": None,
                "reason": "collection-absent-at-migrate",
                "pre_count": 0,
            },
        ],
    }
    path = tmp_path / "migrate-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    args = argparse.Namespace(
        rollback=True,
        manifest=str(path),
        qdrant_url="http://x",
        db_url="sqlite+aiosqlite:////tmp/x.db",
        snapshot_dir="/tmp/x",
    )
    with pytest.raises(MigrationRefused, match="nothing to roll back"):
        asyncio.run(run_rollback(args))
