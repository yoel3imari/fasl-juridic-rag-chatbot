"""Pure unit tests for the todo-6 quality gate (no services, no corpus).

CLI wiring (capture/score) is verified live via the acceptance commands;
these tests pin the leakage guards, the served-safety estimate, the
dense-only recall math, and the capture-file validation.
"""

from __future__ import annotations

import argparse
import json

import pytest

from app.cli.library import quality_gate as qg


def test_length_class_boundaries() -> None:
    assert qg.length_class(0) == "short"
    assert qg.length_class(599) == "short"
    assert qg.length_class(600) == "medium"
    assert qg.length_class(1300) == "medium"
    assert qg.length_class(1301) == "long"


def test_make_query_rejects_short_text() -> None:
    assert qg.make_query("word " * 10) is None
    assert qg.make_query("word " * (qg.MIN_QUERY_WORDS - 1)) is None


def test_make_query_starts_at_or_after_word_25() -> None:
    text = " ".join(f"w{i}" for i in range(200))
    made = qg.make_query(text)
    assert made is not None
    query, start, end = made
    assert start >= qg.QUERY_START_MIN
    assert end - start >= qg.QUERY_LEN // 2
    # the heading / first ~20 tokens never leak into the query
    assert "w0" not in query.split() and "w19" not in query.split()


def test_served_estimate_rejects_newline_heavy_killer() -> None:
    # Empirical killer: ~426 local tokens + 67 newlines dropped the server.
    killer = ("word " * 60 + "\n") * 67
    assert qg.served_estimate(killer) > qg.SERVED_SAFE_TOKENS
    normal = " ".join(f"w{i}" for i in range(120))
    assert qg.served_estimate(normal) <= qg.SERVED_SAFE_TOKENS


def test_recall_at_k_perfect_self_retrieval() -> None:
    vecs = [[1.0 if i == j else 0.0 for j in range(8)] for i in range(8)]
    assert qg._recall_at_k(vecs, vecs, 10) == pytest.approx(1.0)


def test_recall_at_k_miss_when_gold_buried() -> None:
    # Every query is orthogonal to its own chunk but identical to chunk 0:
    # only query 0 hits, the rest rank their gold chunk last.
    n = 5
    chunks = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    queries = [list(chunks[0]) for _ in range(n)]
    assert qg._recall_at_k(chunks, queries, 1) == pytest.approx(1 / n)


def test_cosine_basic() -> None:
    assert qg._cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert qg._cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert qg._cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def _capture_payload(tmp_path, n: int = 100, start_word: int = 25) -> dict:
    return {
        "model": qg.GRANITE_MODEL,
        "dim": qg.GRANITE_DIM,
        "n": n,
        "sample": [
            {
                "chunk_id": f"c{i}",
                "bucket": "short|ocr=no",
                "query_start_word": start_word,
                "query_end_word": start_word + 40,
                "query": "q",
            }
            for i in range(n)
        ],
        "chunk_vectors": [[1.0, 0.0]] * n,
        "query_vectors": [[1.0, 0.0]] * n,
    }


def test_load_capture_missing_raises(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="missing"):
        qg._load_capture(str(tmp_path / "nope.json"), qg.GRANITE_MODEL, qg.GRANITE_DIM)


def test_load_capture_rejects_wrong_model_and_thin_n(tmp_path) -> None:
    p = tmp_path / "g.json"
    bad = _capture_payload(tmp_path)
    bad["model"] = "something-else"
    p.write_text(json.dumps(bad))
    with pytest.raises(RuntimeError, match="want"):
        qg._load_capture(str(p), qg.GRANITE_MODEL, qg.GRANITE_DIM)
    thin = _capture_payload(tmp_path, n=10)
    p.write_text(json.dumps(thin))
    with pytest.raises(RuntimeError, match="< 100"):
        qg._load_capture(str(p), qg.GRANITE_MODEL, qg.GRANITE_DIM)


def test_load_capture_rejects_leaking_query(tmp_path) -> None:
    p = tmp_path / "g.json"
    bad = _capture_payload(tmp_path, start_word=3)
    p.write_text(json.dumps(bad))
    with pytest.raises(RuntimeError, match="lexical leakage"):
        qg._load_capture(str(p), qg.GRANITE_MODEL, qg.GRANITE_DIM)


def test_load_capture_happy_path(tmp_path) -> None:
    p = tmp_path / "g.json"
    p.write_text(json.dumps(_capture_payload(tmp_path)))
    got = qg._load_capture(str(p), qg.GRANITE_MODEL, qg.GRANITE_DIM)
    assert got["n"] == 100


def _ns(**kw) -> argparse.Namespace:
    return argparse.Namespace(**kw)


def test_score_missing_capture_fails(tmp_path) -> None:
    rc = qg.cmd_score(
        _ns(
            granite=str(tmp_path / "nope.json"),
            bge=str(tmp_path / "nope2.json"),
            allow_concurrent=True,
        )
    )
    assert rc == qg.FAIL_EXIT


def test_score_refuses_concurrent_services(tmp_path, monkeypatch) -> None:
    g = tmp_path / "g.json"
    b = tmp_path / "b.json"
    payload = _capture_payload(tmp_path)
    g.write_text(json.dumps(payload))
    b_payload = dict(payload, model=qg.BGE_MODEL, dim=qg.BGE_DIM)
    b.write_text(json.dumps(b_payload))
    monkeypatch.setattr(qg, "_service_healthy", lambda url: True)
    rc = qg.cmd_score(_ns(granite=str(g), bge=str(b), allow_concurrent=False))
    assert rc == qg.FAIL_EXIT


def test_score_rejects_sample_mismatch(tmp_path) -> None:
    g = tmp_path / "g.json"
    b = tmp_path / "b.json"
    g.write_text(json.dumps(_capture_payload(tmp_path)))
    other = _capture_payload(tmp_path)
    other["sample"] = [dict(e, chunk_id="zzz") for e in other["sample"]]
    b.write_text(json.dumps(other))
    rc = qg.cmd_score(_ns(granite=str(g), bge=str(b), allow_concurrent=True))
    assert rc == qg.FAIL_EXIT
