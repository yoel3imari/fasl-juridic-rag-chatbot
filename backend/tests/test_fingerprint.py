from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.cli.library.fingerprint import (
    PIPELINE_FILES,
    compute_pipeline_code_hash,
    compute_pipeline_fingerprint,
    live_git_sha,
    matches_preflight_sha,
    repo_root,
)


def test_repo_root_resolves():
    root = repo_root()
    assert (root / "backend").is_dir()


def test_fingerprint_deterministic():
    fp1 = compute_pipeline_fingerprint("granite-embedding-107m", 384)
    fp2 = compute_pipeline_fingerprint("granite-embedding-107m", 384)
    assert len(fp1) == 40
    assert fp1 == fp2


def test_fingerprint_changes_on_model():
    fp1 = compute_pipeline_fingerprint("granite-embedding-107m", 384)
    fp2 = compute_pipeline_fingerprint("bge-m3", 384)
    assert fp1 != fp2


def test_fingerprint_changes_on_dim():
    fp1 = compute_pipeline_fingerprint("granite-embedding-107m", 384)
    fp2 = compute_pipeline_fingerprint("granite-embedding-107m", 768)
    assert fp1 != fp2


def test_fingerprint_changes_on_code_change():
    root = repo_root()
    base_hash = compute_pipeline_code_hash(root)

    # In a mock directory with one modified pipeline file:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmproot = Path(tmpdir)
        # Recreate pipeline file structure
        for rel in PIPELINE_FILES:
            target = tmproot / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("print('test content')\n", encoding="utf-8")

        hash1 = compute_pipeline_code_hash(tmproot)

        # Mutate one file
        sample_file = tmproot / PIPELINE_FILES[0]
        sample_file.write_text("print('modified content')\n", encoding="utf-8")
        hash2 = compute_pipeline_code_hash(tmproot)

        assert hash1 != hash2
        assert hash1 != base_hash


def test_fingerprint_ignores_unrelated_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmproot = Path(tmpdir)
        for rel in PIPELINE_FILES:
            target = tmproot / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("stable content\n", encoding="utf-8")

        fp1 = compute_pipeline_fingerprint("granite-embedding-107m", 384, root=tmproot)

        # Create/modify unrelated files (e.g. frontend, docs)
        (tmproot / "frontend").mkdir()
        (tmproot / "frontend" / "page.tsx").write_text("export default function() {}", encoding="utf-8")
        (tmproot / "README.md").write_text("# Documentation update", encoding="utf-8")

        fp2 = compute_pipeline_fingerprint("granite-embedding-107m", 384, root=tmproot)

        assert fp1 == fp2


def test_matches_preflight_sha():
    model = "granite-embedding-107m"
    dim = 384
    fp = compute_pipeline_fingerprint(model, dim)

    # 1. Matches live fingerprint
    assert matches_preflight_sha(fp, model=model, dim=dim) is True

    # 2. Matches caller-passed live_sha fixture
    assert matches_preflight_sha("fixture-sha", model=model, dim=dim, live_sha="fixture-sha") is True

    # 3. Matches live git HEAD commit if available
    head = live_git_sha()
    if head != "unknown":
        assert matches_preflight_sha(head, model=model, dim=dim) is True

    # 4. Rejects stale or arbitrary SHA
    assert matches_preflight_sha("deadbeef1234", model=model, dim=dim) is False
    assert matches_preflight_sha(None, model=model, dim=dim) is False
    assert matches_preflight_sha("", model=model, dim=dim) is False
