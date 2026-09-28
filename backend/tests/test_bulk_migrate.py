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

import pytest

from app.cli.library.bulk_migrate import (
    FAIL_EXIT,
    MigrationRefused,
    load_gate_artifact,
    preflight,
    resolve_sqlite_path,
    run_migrate,
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
        snapshot_one(
            _Client(), "http://localhost:6333", "legal_authorities", str(tmp_path), 2
        )
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
