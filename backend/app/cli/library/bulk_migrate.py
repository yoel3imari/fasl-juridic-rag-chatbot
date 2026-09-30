"""Gated destructive collection migration: snapshot -> recreate -> indexes.

Plan todo 8. ``python -m app.cli.library.bulk migrate`` refuses unless BOTH
gate artifacts are fresh and green, then snapshots BOTH collections
(API download + sha256, asserted BEFORE any delete), backs up the SQLite
ledger, recreates both collections at the gate-winner dim (dense COSINE +
sparse ``lexical``), creates the authority payload indexes, and writes a
``library_import_runs`` sentinel row (kind=``migrate``).

``migrate --rollback --manifest <path>`` recovers both collections from
their server-side snapshots and asserts counts return to pre-migration
values. Rollback is restorative: it does not require the gate.

Collections absent from Qdrant skip the snapshot (nothing to lose) and are
created at the winner dim; the manifest records them under ``cold_start``
and rollback refuses them (nothing to restore).

Exit codes: 0 on success, 2 (``FAIL_EXIT``) on any refusal or failure.
No delete ever happens after a failed/missing gate or snapshot export.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FAIL_EXIT = 2

EVIDENCE_COLLECTION = "matter_evidence"
AUTHORITY_COLLECTION = "legal_authorities"
COLLECTIONS = (EVIDENCE_COLLECTION, AUTHORITY_COLLECTION)

SPARSE_NAME = "lexical"
AUTHORITY_PAYLOAD_INDEXES = ("source", "edition", "language", "category")

MIN_PARITY_COSINE = 0.99
SNAPSHOT_POLL_SECONDS = 300.0
RECOVER_POLL_SECONDS = 300.0

DEFAULT_SNAPSHOT_DIR = "./data/migrate-snapshots"


class MigrationRefused(Exception):
    """Preflight or safety-gate refusal: exit non-zero BEFORE any delete."""


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))


def live_git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=_repo_root(),
            timeout=15,
        )
        sha = out.stdout.strip()
        if out.returncode == 0 and sha:
            return sha
    except Exception:
        pass
    return "unknown"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_gate_artifact(path: str, kind: str) -> dict:
    """Load a gate artifact or refuse loudly (missing/unparseable never passes)."""
    if not path or not os.path.exists(path):
        raise MigrationRefused(f"{kind} artifact missing: {path!r} (refusing)")
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        raise MigrationRefused(f"{kind} artifact unparseable: {path!r}: {exc}")
    if not isinstance(payload, dict):
        raise MigrationRefused(f"{kind} artifact is not a JSON object: {path!r}")
    return payload


def preflight(
    gate: dict,
    parity: dict,
    *,
    model: str,
    dim: int,
    sha: str,
) -> dict:
    """Validate BOTH artifacts against the live config. Raises MigrationRefused.

    Requires quality_gate ``pass:true``, parity ``mean_cosine>=0.99``,
    BOTH ``git_sha`` equal to the live HEAD, and both ``model``/``dim``
    matching the live ``EMBEDDING_MODEL``/``EMBEDDING_DIM``.
    """
    problems: list[str] = []
    if gate.get("pass") is not True:
        problems.append(f"quality_gate pass={gate.get('pass')!r} (want True)")
    if gate.get("model") != model:
        problems.append(f"quality_gate model={gate.get('model')!r} != live {model!r}")
    if gate.get("dim") != dim:
        problems.append(f"quality_gate dim={gate.get('dim')!r} != live {dim!r}")
    if gate.get("git_sha") != sha:
        problems.append(
            f"quality_gate git_sha={gate.get('git_sha')!r} != live HEAD {sha!r} (stale)"
        )
    mean_cosine = parity.get("mean_cosine")
    if not isinstance(mean_cosine, (int, float)) or mean_cosine < MIN_PARITY_COSINE:
        problems.append(f"parity mean_cosine={mean_cosine!r} < {MIN_PARITY_COSINE} (refusing)")
    if parity.get("model") != model:
        problems.append(f"parity model={parity.get('model')!r} != live {model!r}")
    if parity.get("dim") != dim:
        problems.append(f"parity dim={parity.get('dim')!r} != live {dim!r}")
    if parity.get("git_sha") != sha:
        problems.append(f"parity git_sha={parity.get('git_sha')!r} != live HEAD {sha!r} (stale)")
    if problems:
        raise MigrationRefused("; ".join(problems))
    return {
        "model": model,
        "dim": dim,
        "git_sha": sha,
        "gate_recall_granite": gate.get("recall_at_10_granite"),
        "gate_recall_bge": gate.get("recall_at_10_bge"),
        "gate_delta_points": gate.get("delta_points"),
        "parity_mean_cosine": mean_cosine,
    }


def _client_for(url: str):
    from qdrant_client import QdrantClient

    return QdrantClient(url=url.rstrip("/"), timeout=30)


def collection_state(client: Any, name: str) -> dict:
    """Read live collection config via API (assertions use reads, not log prose).

    A missing collection is a state, not an error: cold-start Qdrant (fresh
    volume, no collections yet) returns ``exists: False`` with empty config
    so ``run_migrate`` can create instead of crashing before its own logic.
    Any non-404 failure still raises.
    """
    try:
        info = client.get_collection(name)
    except Exception as exc:
        if getattr(exc, "status_code", None) != 404:
            raise
        return {
            "name": name,
            "exists": False,
            "dense_dim": None,
            "dense_distance": None,
            "sparse": [],
            "on_disk_payload": None,
            "payload_indexes": [],
            "points_count": 0,
        }
    vectors = info.config.params.vectors
    if isinstance(vectors, dict):
        dense = vectors["dense"]
    else:
        dense = vectors.dense if hasattr(vectors, "dense") else vectors
    dense_size: Any = dense.size if hasattr(dense, "size") else dense["size"]
    distance: Any = dense.distance if hasattr(dense, "distance") else dense["distance"]
    distance = str(distance)
    sparse = info.config.params.sparse_vectors or {}
    sparse_names = sorted(sparse.keys() if hasattr(sparse, "keys") else sparse)
    payload_schema = info.payload_schema or {}
    on_disk = info.config.params.on_disk_payload
    count = client.count(name, exact=True).count
    return {
        "name": name,
        "exists": True,
        "dense_dim": dense_size,
        "dense_distance": distance,
        "sparse": sparse_names,
        "on_disk_payload": on_disk,
        "payload_indexes": sorted(payload_schema.keys()),
        "points_count": count,
    }


def snapshot_one(client: Any, base_url: str, name: str, dest_dir: str, pre_count: int) -> dict:
    """Create + poll + API-download one snapshot; assert non-zero BEFORE return.

    Raises MigrationRefused/RuntimeError on any export failure so the caller
    never proceeds to a delete.
    """
    desc = client.create_snapshot(name, wait=True)
    snap_name: str = desc.name
    deadline = time.time() + SNAPSHOT_POLL_SECONDS
    while time.time() < deadline:
        listed = {s.name for s in client.list_snapshots(name)}
        if snap_name in listed:
            break
        time.sleep(2.0)
    else:
        raise MigrationRefused(
            f"snapshot {snap_name!r} for {name} never completed "
            f"(absent from snapshot list after {SNAPSHOT_POLL_SECONDS:.0f}s)"
        )
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"{name}-{snap_name}.snapshot")
    url = (
        base_url.rstrip("/")
        + "/collections/"
        + urllib.parse.quote(name)
        + "/snapshots/"
        + urllib.parse.quote(snap_name)
    )
    digest = hashlib.sha256()
    size = 0
    try:
        with urllib.request.urlopen(url, timeout=600) as resp, open(dest, "wb") as fh:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                fh.write(chunk)
    except Exception as exc:
        raise MigrationRefused(f"snapshot download failed for {name}: {exc}")
    if size == 0 or not os.path.exists(dest):
        raise MigrationRefused(f"snapshot download empty/missing for {name} (size={size})")
    return {
        "collection": name,
        "snapshot_name": snap_name,
        "file": dest,
        "sha256": digest.hexdigest(),
        "bytes": size,
        "pre_count": pre_count,
    }


def resolve_sqlite_path(db_url: str) -> str:
    """Resolve a SQLAlchemy sqlite URL to an absolute host path (refuse if not)."""
    if not db_url.startswith("sqlite"):
        raise MigrationRefused(f"DATABASE_URL is not sqlite: {db_url!r}")
    path = db_url.split(":///", 1)[-1].split("?", 1)[0]
    if path == ":memory:" or not path:
        raise MigrationRefused(f"refusing to back up non-file DB: {db_url!r}")
    return os.path.abspath(path)


def backup_sqlite(src: str, dest_dir: str) -> dict:
    """Copy the live DB file to a timestamped path; assert it is the live DB."""
    if not os.path.isfile(src):
        raise MigrationRefused(f"live DB not found at resolved path: {src!r}")
    st = os.stat(src)
    if st.st_size == 0:
        raise MigrationRefused(f"live DB is empty: {src!r}")
    # Assert this is the ledger DB, not a stray file: it must carry the
    # alembic + library tables the API migrates.
    from app.repositories.library_import import (
        LEDGER_REQUIRED_TABLES,
        probe_ledger_tables,
    )

    ledger_ok = False
    try:
        tables = probe_ledger_tables(src)
        ledger_ok = LEDGER_REQUIRED_TABLES.issubset(tables)
    except Exception as exc:
        raise MigrationRefused(f"cannot probe live DB {src!r}: {exc}")
    if not ledger_ok:
        raise MigrationRefused(
            f"{src!r} lacks alembic_version/library_import_runs (not the live ledger DB; refusing)"
        )
    os.makedirs(dest_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = os.path.join(dest_dir, f"matters-backup-{stamp}.db")
    shutil.copy2(src, dest)
    digest = hashlib.sha256()
    with open(dest, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return {
        "src": src,
        "src_bytes": st.st_size,
        "src_mtime": st.st_mtime,
        "dest": dest,
        "dest_sha256": digest.hexdigest(),
    }


def _create_collection(client: Any, name: str, dim: int) -> dict:
    """Create one collection at ``dim`` and verify dim + lexical; return state."""
    from qdrant_client.models import (
        Distance,
        HnswConfigDiff,
        OptimizersConfigDiff,
        SparseVectorParams,
        VectorParams,
    )

    client.create_collection(
        collection_name=name,
        vectors_config={"dense": VectorParams(size=dim, distance=Distance.COSINE)},
        sparse_vectors_config={SPARSE_NAME: SparseVectorParams()},
        on_disk_payload=True,
        hnsw_config=HnswConfigDiff(m=16, ef_construct=100),
        optimizers_config=OptimizersConfigDiff(indexing_threshold=10000),
    )
    state = collection_state(client, name)
    if state["dense_dim"] != dim:
        raise RuntimeError(f"create verification failed for {name}: dim={state['dense_dim']!r}")
    if SPARSE_NAME not in state["sparse"]:
        raise RuntimeError(f"create verification failed for {name}: no lexical")
    return state


def recreate_collection(client: Any, name: str, dim: int) -> dict:
    """Delete + recreate one collection at ``dim`` (mirrors live HNSW/optimizer)."""
    client.delete_collection(name)
    return _create_collection(client, name, dim)


def ensure_authority_indexes(client: Any) -> list[str]:
    """Create the 4 authority payload indexes (keyword); return the live list."""
    from qdrant_client.models import PayloadSchemaType

    for field in AUTHORITY_PAYLOAD_INDEXES:
        client.create_payload_index(
            AUTHORITY_COLLECTION,
            field_name=field,
            field_schema=PayloadSchemaType.KEYWORD,
            wait=True,
        )
    state = collection_state(client, AUTHORITY_COLLECTION)
    missing = [f for f in AUTHORITY_PAYLOAD_INDEXES if f not in state["payload_indexes"]]
    if missing:
        raise RuntimeError(f"payload indexes missing after create: {missing}")
    return state["payload_indexes"]


async def write_sentinel(
    db_url: str,
    *,
    kind: str,
    model: str,
    dim: int,
    sha: str,
    status: str,
    payload: dict,
) -> int:
    """Write a ``library_import_runs`` row; return its id."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401  (register ledger metadata)
    from app.models.base import Base
    from app.models.library_import import LibraryImportRun

    engine = create_async_engine(db_url, connect_args={"timeout": 30})
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            row = LibraryImportRun(
                kind=kind,
                model=model,
                dim=dim,
                git_sha=sha,
                status=status,
                payload_json=payload,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return int(row.id)
    finally:
        await engine.dispose()


async def run_migrate(args: argparse.Namespace) -> dict:
    """Execute ``migrate [--confirm]`` or ``migrate --rollback``."""
    from app.config import settings

    if args.rollback:
        return await run_rollback(args)
    if not args.confirm:
        raise MigrationRefused("refusing: pass --confirm to run the migration")

    gate = load_gate_artifact(args.gate, "quality_gate")
    parity = load_gate_artifact(args.parity, "parity")
    model = settings.EMBEDDING_MODEL
    dim = settings.EMBEDDING_DIM
    sha = live_git_sha()
    gate_summary = preflight(gate, parity, model=model, dim=dim, sha=sha)

    qdrant_url = args.qdrant_url or settings.QDRANT_URL
    db_url = args.db_url or settings.DATABASE_URL
    snapshot_dir = os.path.abspath(args.snapshot_dir or DEFAULT_SNAPSHOT_DIR)

    client = _client_for(qdrant_url)
    try:
        pre = {name: collection_state(client, name) for name in COLLECTIONS}
    finally:
        client.close()
    missing = [n for n in COLLECTIONS if not pre[n]["exists"]]

    # Fast path: winner dim already live -> verify only, no data loss.
    if not missing and all(pre[name]["dense_dim"] == dim for name in COLLECTIONS):
        client = _client_for(qdrant_url)
        try:
            indexes = ensure_authority_indexes(client)
            post = {name: collection_state(client, name) for name in COLLECTIONS}
        finally:
            client.close()
        row_id = await write_sentinel(
            db_url,
            kind="migrate",
            model=model,
            dim=dim,
            sha=sha,
            status="done",
            payload={
                "skipped_recreate": True,
                "reason": "winner dim already live",
                "counts": {n: post[n]["points_count"] for n in COLLECTIONS},
            },
        )
        return {
            "action": "migrate",
            "skipped_recreate": True,
            "pre": pre,
            "post": post,
            "payload_indexes": indexes,
            "sentinel_id": row_id,
            "gate": gate_summary,
        }

    # Destructive path: snapshot-download + sha256 BEFORE any delete.
    # Cold-start collections (absent from Qdrant) hold nothing to lose:
    # they skip the snapshot and are created at the winner dim.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = os.path.join(snapshot_dir, f"migrate-{stamp}")
    present = [n for n in COLLECTIONS if n not in missing]
    snap_taken: dict[str, dict] = {}
    if present:
        client = _client_for(qdrant_url)
        try:
            for name in present:
                snap_taken[name] = snapshot_one(
                    client, qdrant_url, name, run_dir, pre[name]["points_count"]
                )
        finally:
            client.close()
    snapshots = [
        snap_taken.get(
            name,
            {
                "collection": name,
                "snapshot_name": None,
                "reason": "collection-absent-at-migrate",
                "pre_count": 0,
                "bytes": 0,
            },
        )
        for name in COLLECTIONS
    ]

    backup = backup_sqlite(resolve_sqlite_path(db_url), run_dir)

    client = _client_for(qdrant_url)
    try:
        for name in COLLECTIONS:
            if name in missing:
                _create_collection(client, name, dim)
            else:
                recreate_collection(client, name, dim)
        indexes = ensure_authority_indexes(client)
        post = {name: collection_state(client, name) for name in COLLECTIONS}
    finally:
        client.close()

    manifest = {
        "action": "migrate",
        "model": model,
        "dim": dim,
        "git_sha": sha,
        "produced_at": _now(),
        "qdrant_url": qdrant_url,
        "cold_start": missing,
        "snapshots": snapshots,
        "sqlite_backup": backup,
        "pre_counts": {n: pre[n]["points_count"] for n in COLLECTIONS},
        "pre_dims": {n: pre[n]["dense_dim"] for n in COLLECTIONS},
        "post_counts": {n: post[n]["points_count"] for n in COLLECTIONS},
        "post_dims": {n: post[n]["dense_dim"] for n in COLLECTIONS},
        "payload_indexes": indexes,
        "gate": gate_summary,
    }
    manifest_path = os.path.join(run_dir, "migrate-manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    row_id = await write_sentinel(
        db_url,
        kind="migrate",
        model=model,
        dim=dim,
        sha=sha,
        status="done",
        payload={
            "manifest": manifest_path,
            "cold_start": missing,
            "snapshots": [
                {"collection": s["collection"], "snapshot_name": s["snapshot_name"]}
                for s in snapshots
            ],
            "pre_counts": manifest["pre_counts"],
            "post_counts": manifest["post_counts"],
        },
    )
    manifest["manifest_path"] = manifest_path
    manifest["sentinel_id"] = row_id
    return manifest


async def run_rollback(args: argparse.Namespace) -> dict:
    """Recover both collections from a migrate manifest's server-side snapshots."""
    from app.config import settings

    manifest_path = args.manifest or _newest_manifest(
        os.path.abspath(args.snapshot_dir or DEFAULT_SNAPSHOT_DIR)
    )
    if not manifest_path or not os.path.exists(manifest_path):
        raise MigrationRefused(
            f"rollback manifest missing: {manifest_path!r} "
            f"(pass --manifest <migrate-manifest.json>)"
        )
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    snapshots = {s["collection"]: s for s in manifest.get("snapshots", [])}
    missing = [n for n in COLLECTIONS if n not in snapshots]
    if missing:
        raise MigrationRefused(f"rollback manifest lacks snapshots for: {missing} (refusing)")
    unsnapshotted = [n for n in COLLECTIONS if not snapshots[n].get("snapshot_name")]
    if unsnapshotted:
        raise MigrationRefused(
            f"no snapshot recorded for: {unsnapshotted} (cold-start create; nothing to roll back)"
        )

    qdrant_url = args.qdrant_url or settings.QDRANT_URL
    db_url = args.db_url or settings.DATABASE_URL
    # Server-side snapshot dir for this Qdrant image is /qdrant/snapshots
    # (NOT /qdrant/storage/snapshots): recover takes a URL or file:// path,
    # never a bare snapshot name.
    server_snapshot_dir = getattr(args, "server_snapshot_dir", None) or (
        os.environ.get("QDRANT_SERVER_SNAPSHOT_DIR") or "/qdrant/snapshots"
    )
    client = _client_for(qdrant_url)
    try:
        for name in COLLECTIONS:
            snap = snapshots[name]["snapshot_name"]
            # Recover replaces the collection: the live collection (created
            # at the migrated dim) is dim-incompatible with the snapshot by
            # design, so it must be deleted first. The snapshot was verified
            # present server-side (list_snapshots) and hash-asserted on
            # download before any delete ever happened.
            listed = {s.name for s in client.list_snapshots(name)}
            if snap not in listed:
                raise MigrationRefused(
                    f"server-side snapshot {snap!r} for {name} is gone "
                    f"(refusing rollback without a verified snapshot)"
                )
            client.delete_collection(name)
            location = f"file://{server_snapshot_dir}/{name}/{snap}"
            ok = client.recover_snapshot(name, location, wait=True)
            if ok is False:
                raise RuntimeError(f"recover_snapshot returned false for {name}")
        deadline = time.time() + RECOVER_POLL_SECONDS
        restored: dict[str, dict] = {}
        while time.time() < deadline:
            restored = {name: collection_state(client, name) for name in COLLECTIONS}
            want = {n: snapshots[n]["pre_count"] for n in COLLECTIONS}
            if all(restored[n]["points_count"] == want[n] for n in COLLECTIONS):
                break
            await asyncio.sleep(2.0)
        else:
            raise RuntimeError(
                "rollback counts did not return to pre-migration values: "
                + json.dumps({n: restored[n]["points_count"] for n in COLLECTIONS})
            )
    finally:
        client.close()

    sha = live_git_sha()
    row_id = await write_sentinel(
        db_url,
        kind="migrate",
        model=manifest.get("model", ""),
        dim=int(manifest.get("dim", 0)),
        sha=sha,
        status="rolled-back",
        payload={
            "manifest": manifest_path,
            "restored_counts": {n: restored[n]["points_count"] for n in COLLECTIONS},
            "restored_dims": {n: restored[n]["dense_dim"] for n in COLLECTIONS},
        },
    )
    return {
        "action": "rollback",
        "manifest": manifest_path,
        "restored_counts": {n: restored[n]["points_count"] for n in COLLECTIONS},
        "restored_dims": {n: restored[n]["dense_dim"] for n in COLLECTIONS},
        "expected_counts": {n: snapshots[n]["pre_count"] for n in COLLECTIONS},
        "sentinel_id": row_id,
    }


def _newest_manifest(snapshot_dir: str) -> str | None:
    cands = sorted(Path(snapshot_dir).glob("migrate-*/migrate-manifest.json"))
    return str(cands[-1]) if cands else None


def startup_dim_check() -> dict:
    """Maintenance-safe startup dim assert: log-and-degrade, NEVER raise.

    Skipped under the named migration lock (``FASL_MIGRATION_LOCK=1``).
    A live dim mismatch is logged as a warning (degraded reads) and returned;
    startup must never crash-loop because of it.
    """
    try:
        if os.environ.get("FASL_MIGRATION_LOCK") == "1":
            return {"checked": False, "reason": "FASL_MIGRATION_LOCK=1"}
        from app.config import settings

        client = _client_for(settings.QDRANT_URL)
        try:
            live = {name: collection_state(client, name) for name in COLLECTIONS}
        finally:
            client.close()
        mismatched = [
            name for name in COLLECTIONS if live[name]["dense_dim"] != settings.EMBEDDING_DIM
        ]
        sentinel: dict[str, Any] = {"checked": False, "reason": "sentinel-unread"}
        try:
            from app.repositories.library_import import read_migrate_sentinel

            src = resolve_sqlite_path(settings.DATABASE_URL)
            rows = read_migrate_sentinel(src)
            if rows:
                k, m, d, g, s = rows[0]
                sentinel = {
                    "checked": True,
                    "kind": k,
                    "model": m,
                    "dim": d,
                    "git_sha": g,
                    "status": s,
                }
        except Exception as exc:
            sentinel = {"checked": False, "reason": f"sentinel-unread: {exc}"}
        result = {
            "checked": True,
            "live_dims": {n: live[n]["dense_dim"] for n in COLLECTIONS},
            "configured_dim": settings.EMBEDDING_DIM,
            "configured_model": settings.EMBEDDING_MODEL,
            "mismatched": mismatched,
            "sentinel": sentinel,
        }
        if mismatched:
            logger.warning(
                "dim mismatch (degraded reads, no crash): live=%s configured=%s "
                "sentinel=%s; run `bulk migrate --confirm` or set "
                "FASL_MIGRATION_LOCK=1 during maintenance",
                result["live_dims"],
                settings.EMBEDDING_DIM,
                sentinel,
            )
        return result
    except Exception as exc:
        logger.warning("startup dim check degraded (no crash): %s", exc)
        return {"checked": False, "reason": f"degraded: {exc}"}


def add_migrate_parser(sub: Any) -> argparse.ArgumentParser:
    parser = sub.add_parser(
        "migrate",
        help="gated snapshot -> recreate both collections -> payload indexes",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="required: acknowledge the destructive recreate",
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="restore both collections from a migrate manifest's snapshots",
    )
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--qdrant-url", default=None)
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--snapshot-dir", default=None)
    parser.add_argument("--gate", default="quality_gate.json")
    parser.add_argument("--parity", default="parity.json")
    parser.add_argument(
        "--server-snapshot-dir",
        default=None,
        help="Qdrant container snapshot dir for --rollback (default /qdrant/snapshots)",
    )
    return parser


async def run_migrate_from_args(args: argparse.Namespace) -> dict:
    try:
        return await run_migrate(args)
    except MigrationRefused as exc:
        print(f"FAIL: migrate refused: {exc}", file=sys.stderr)
        raise SystemExit(FAIL_EXIT)
