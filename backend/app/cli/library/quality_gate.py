"""Granite vs bge-m3 retrieval quality gate (plan todo 6).

Label-free self-retrieval over a STRATIFIED deterministic sample of >=100
real corpus chunks (from the todo-12 zstd artifacts): bucket key =
(length_class in {short,medium,long} x ocr_flag in {no,yes}) = 6 buckets,
>=15 per covered bucket. Infeasible buckets are DROPPED explicitly with a
recorded reason and the claim is capped to the covered buckets.

Queries are NON-TRIVIAL: a mid-article word span starting at word offset
>=25 -- never the heading and never the first ~20 tokens (lexical
leakage). Scoring is DENSE-ONLY (cosine over cached embedding vectors,
in-memory; the sparse/lexical path is never consulted).

Two-phase, never-concurrent usage on this RAM-constrained host (only ONE
GGUF in RAM at a time)::

    # Phase A (granite up on :8080): cache granite vectors
    cd backend && uv run python -m app.cli.library.quality_gate capture \\
        --model granite --out gate-granite.json
    # Choreography: stop granite, start bge
    #   docker compose stop crispembed
    #   docker compose up -d crispembed-bge   # wait for :8081/health
    # Phase B (bge up on :8081): cache bge vectors
    cd backend && uv run python -m app.cli.library.quality_gate capture \\
        --model bge --out gate-bge.json
    # Choreography: stop bge, restore granite, then score OFFLINE
    cd backend && uv run python -m app.cli.library.quality_gate score \\
        --granite gate-granite.json --bge gate-bge.json \\
        --out quality_gate.json

Hard rule: if granite recall@10 is >5.0 points below bge-m3, ``pass=false``
and the pipeline stays on bge-m3. ``score`` refuses to run while BOTH
services answer health (concurrency guard) and on missing/tampered caches
(a bad cache can never false-pass). Exit 0 iff ``pass``; exit 2 otherwise.
"""

from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import math
import os
import subprocess
import sys
import urllib.request

GRANITE_MODEL = "granite-embedding-107m"
GRANITE_DIM = 384
GRANITE_URL = "http://localhost:8080"
BGE_MODEL = "bge-m3"
BGE_DIM = 1024
BGE_URL = "http://localhost:8081"

MIN_N = 100
MIN_PER_BUCKET = 15
RECALL_K = 10
TOLERANCE_POINTS = 5.0
FAIL_EXIT = 2

LENGTH_BOUNDS = (600, 1300)  # chars: short <600, medium <=1300, else long
MIN_QUERY_WORDS = 65  # chunks with fewer words are ineligible (leakage guard)
QUERY_START_MIN = 25  # query span never starts before word 25 (no first-~20 leak)
QUERY_LEN = 40

OCR_MIN_CHARS_PER_PAGE = 20  # mirrors LIBRARY_OCR_MIN_CHARS text-layer check

# Served-context safety: CrispEmbed-Granite DROPS the connection (no
# server-side truncation, and a killer input can crash-loop the server) for
# inputs past ~512 served tokens. The served tokenization counts newlines
# much heavier than the local tokenizer: empirically, a chunk at 426 local
# tokens with 67 newlines (naive local+newlines estimate = 493) dropped the
# server, so each newline is weighted x4. Private-use (Co) characters were
# isolated as a suspect and EXONERATED (all Co chunks embed fine).
# A second killer family (2026-09-30, file id 110 p5:o6 + p19:o30):
# OCR-spaced tatweel artifacts (e.g. "م ـصـ ـفـن،ة", "ـكـسـ ال ك ـ ال ـح ـ دي")
# tokenize HEAVIER server-side than the HF snapshot counts locally --
# local 491/499 with zero newlines still crashed the server (underestimate
# >=21 tokens). The cap therefore carries a ~60-token safety margin below
# the 512 served limit instead of sitting at 500.
# Overlong chunks are SKIPPED and counted (never truncated: both models must
# see identical text). Production embedder (todo 14) must handle these.
SERVED_SAFE_TOKENS = 450
NEWLINE_WEIGHT = 4


def served_estimate(text: str) -> int:
    """Conservative served-token estimate: local tokens + weighted newlines."""
    from app.domain.authority.granite_tokens import count_granite_tokens

    return count_granite_tokens(text) + NEWLINE_WEIGHT * text.count("\n")


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))


def _git_sha() -> str:
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
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def length_class(n_chars: int) -> str:
    lo, hi = LENGTH_BOUNDS
    if n_chars < lo:
        return "short"
    if n_chars <= hi:
        return "medium"
    return "long"


def _artifact_dir(explicit: str | None) -> str:
    if explicit:
        return explicit
    try:
        from app.config import settings as _settings

        return str(_settings.LIBRARY_ARTIFACT_DIR)
    except Exception:
        return "./data/library-artifacts"


def _source_dir(explicit: str | None) -> str:
    if explicit:
        return explicit
    try:
        from app.config import settings as _settings

        return str(_settings.LIBRARY_SOURCE_DIR)
    except Exception:
        return "/home/xozev/Documents/legal/shortlist"


def load_corpus_chunks(artifact_dir: str) -> list[dict]:
    """Read every chunk from every zstd artifact (streams per file)."""
    from app.cli.library.artifacts import read_artifact

    chunks: list[dict] = []
    for path in sorted(glob.glob(os.path.join(artifact_dir, "*.jsonl.zst"))):
        for rec in read_artifact(path):
            chunks.append(rec)
    return chunks


def build_sha_to_pdf(source_dir: str) -> dict[str, str]:
    """Map file content-sha256 -> shortlist PDF path (sorted walk, first wins)."""
    mapping: dict[str, str] = {}
    pdfs = sorted(glob.glob(os.path.join(source_dir, "*", "*.pdf")))
    if not pdfs:
        pdfs = sorted(glob.glob(os.path.join(source_dir, "**", "*.pdf"), recursive=True))
    for pdf in pdfs:
        h = hashlib.sha256()
        try:
            with open(pdf, "rb") as f:
                for blk in iter(lambda: f.read(1 << 20), b""):
                    h.update(blk)
        except OSError:
            continue
        mapping.setdefault(h.hexdigest(), pdf)
    return mapping


def pdf_text_chars(pdf_path: str) -> tuple[int, int]:
    """Return (chars, pages) of the raw text layer (no OCR)."""
    import fitz  # PyMuPDF (same style as extractor.py / bulk_catalog.py)

    doc = fitz.open(pdf_path)
    try:
        npages = doc.page_count
        chars = 0
        for i in range(npages):
            chars += len((doc[i].get_text() or "").strip())
        return chars, npages
    finally:
        doc.close()


def ocr_flag_for_pdf(pdf_path: str | None) -> tuple[str, str]:
    """Derive ocr_flag cheaply: empty text layer => OCR was needed.

    Returns (flag, detail). Unknown source => ('unknown', reason).
    """
    if pdf_path is None:
        return "unknown", "no matching source PDF for file_sha"
    try:
        chars, npages = pdf_text_chars(pdf_path)
    except Exception as exc:
        return "unknown", f"text-layer probe failed: {exc}"
    if npages == 0:
        return "yes", "0 pages"
    if chars < OCR_MIN_CHARS_PER_PAGE * npages:
        return "yes", f"text layer {chars} chars over {npages} pages"
    return "no", f"text layer {chars} chars over {npages} pages"


def make_query(text: str) -> tuple[str, int, int] | None:
    """Build the non-trivial query: mid-article word span, never word <25.

    Returns (query, start_word, end_word) or None if the chunk is too short
    to support a leakage-free span.
    """
    words = text.split()
    if len(words) < MIN_QUERY_WORDS:
        return None
    start = max(QUERY_START_MIN, len(words) // 2 - QUERY_LEN // 2)
    end = min(start + QUERY_LEN, len(words))
    if end - start < QUERY_LEN // 2:
        return None
    return " ".join(words[start:end]), start, end


def build_sample(
    artifact_dir: str, source_dir: str, per_bucket: int = 40
) -> tuple[list[dict], list[dict], dict]:
    """Build the stratified deterministic sample.

    Returns (sample, dropped_buckets, stats). Raises on joint infeasibility.
    """
    chunks = load_corpus_chunks(artifact_dir)
    if not chunks:
        raise RuntimeError(f"no chunks found under {artifact_dir}")
    sha_to_pdf = build_sha_to_pdf(source_dir)

    ocr_cache: dict[str, tuple[str, str]] = {}
    buckets: dict[str, list[dict]] = {}
    too_short = 0
    unknown_src = 0
    overlong = 0
    for rec in chunks:
        sha = str(rec.get("file_sha", ""))
        if sha not in ocr_cache:
            ocr_cache[sha] = ocr_flag_for_pdf(sha_to_pdf.get(sha))
        flag, _detail = ocr_cache[sha]
        if flag == "unknown":
            unknown_src += 1
            continue
        text = str(rec.get("text", ""))
        # Served-safe eligibility: identical text must embed on BOTH models;
        # granite drops the connection past its context (no truncation).
        if served_estimate(text) > SERVED_SAFE_TOKENS:
            overlong += 1
            continue
        made = make_query(text)
        if made is None:
            too_short += 1
            continue
        query, qs, qe = made
        key = f"{length_class(len(str(rec.get('text', ''))))}|ocr={flag}"
        buckets.setdefault(key, []).append(
            {
                "chunk_id": rec.get("chunk_id"),
                "file_sha": sha,
                "bucket": key,
                "length_class": key.split("|")[0],
                "ocr_flag": flag,
                "text": rec.get("text"),
                "query": query,
                "query_start_word": qs,
                "query_end_word": qe,
            }
        )
    all_keys = [f"{lc}|ocr={of}" for lc in ("short", "medium", "long") for of in ("no", "yes")]
    sample: list[dict] = []
    dropped: list[dict] = []
    for key in all_keys:
        members = sorted(buckets.get(key, []), key=lambda m: str(m["chunk_id"]))
        if len(members) < MIN_PER_BUCKET:
            reason = (
                f"only {len(members)} eligible chunks (<{MIN_PER_BUCKET}); "
                f"{'shortlist PDFs carry a native text layer, OCR path unused' if key.endswith('ocr=yes') else 'length class under-populated in extracted subset'}"
            )
            dropped.append({"bucket": key, "n_available": len(members), "reason": reason})
            continue
        sample.extend(members[:per_bucket])
    sample.sort(key=lambda m: str(m["chunk_id"]))
    stats = {
        "corpus_chunks": len(chunks),
        "corpus_files": len(ocr_cache),
        "skipped_too_short": too_short,
        "skipped_unknown_source": unknown_src,
        "skipped_overlong_served_context": overlong,
        "covered_buckets": sorted({m["bucket"] for m in sample}),
    }
    if len(sample) < MIN_N:
        raise RuntimeError(
            f"joint infeasibility: n={len(sample)} < {MIN_N} "
            f"(covered={[m for m in stats['covered_buckets']]}, dropped={dropped})"
        )
    return sample, dropped, stats


def _sanitize(text: str) -> str:
    """Payload-copy sanitization for the CrispEmbed naive string parser.

    Mirrors ``app.infrastructure.embeddings.client.sanitize_request_text``: stored/sampled
    text is untouched, only the wire copy is sanitized.
    """
    return text.replace('"', "'").replace("]", ")")


def _post_embeddings(base_url: str, model: str, texts: list[str]) -> list[list[float]]:
    # Small batches: RAM-constrained host; mirrors embedder.MAX_CHARS_PER_REQUEST.
    out: list[list[float]] = []
    i = 0
    while i < len(texts):
        j = i
        chars = 0
        while j < len(texts) and (j - i) < 8 and chars + len(texts[j]) <= 24_000:
            chars += len(texts[j])
            j += 1
        if j == i:
            j = i + 1
        batch = [_sanitize(t) for t in texts[i:j]]
        req = urllib.request.Request(
            base_url.rstrip("/") + "/v1/embeddings",
            data=json.dumps({"input": batch, "model": model}).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.load(resp)
        items = data.get("data", [])
        if len(items) != len(batch):
            raise RuntimeError(f"embedding count mismatch: {len(items)} for {len(batch)}")
        out.extend([list(map(float, it["embedding"])) for it in items])
        i = j
    return out


def _service_healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=5) as resp:
            return resp.status < 400
    except Exception:
        return False


def cmd_capture(args: argparse.Namespace) -> int:
    model = args.model
    if model == "granite":
        model_name, dim, url = (
            GRANITE_MODEL,
            GRANITE_DIM,
            args.crispembed_url or GRANITE_URL,
        )
    elif model == "bge":
        model_name, dim, url = BGE_MODEL, BGE_DIM, args.crispembed_url or BGE_URL
    else:
        print(f"FAIL: --model must be granite|bge, got {model!r}", file=sys.stderr)
        return FAIL_EXIT
    if args.expect_dim != dim:
        print(
            f"FAIL: {model} dim is {dim}, --expect-dim {args.expect_dim} refuses",
            file=sys.stderr,
        )
        return FAIL_EXIT
    if not _service_healthy(url):
        print(
            f"FAIL: {model} service not healthy at {url} (start it first)",
            file=sys.stderr,
        )
        return FAIL_EXIT
    try:
        sample, dropped, stats = build_sample(
            args.artifact_dir, args.source_dir, per_bucket=args.per_bucket
        )
    except Exception as exc:
        print(f"FAIL: sampling: {exc}", file=sys.stderr)
        return FAIL_EXIT
    texts = [m["text"] for m in sample]
    queries = [m["query"] for m in sample]
    try:
        chunk_vecs = _post_embeddings(url, model_name, texts)
        query_vecs = _post_embeddings(url, model_name, queries)
    except Exception as exc:
        print(f"FAIL: {model} capture failed: {exc}", file=sys.stderr)
        return FAIL_EXIT
    if any(len(v) != dim for v in chunk_vecs + query_vecs):
        print(f"FAIL: {model} dim mismatch, want {dim}", file=sys.stderr)
        return FAIL_EXIT
    payload = {
        "model": model_name,
        "dim": dim,
        "crispembed_url": url,
        "git_sha": _git_sha(),
        "produced_at": _now(),
        "n": len(sample),
        "buckets": sorted({m["bucket"] for m in sample}),
        "dropped_buckets": dropped,
        "sampling_stats": stats,
        "sample": [
            {
                k: m[k]
                for k in (
                    "chunk_id",
                    "bucket",
                    "query_start_word",
                    "query_end_word",
                    "query",
                )
            }
            for m in sample
        ],
        "chunk_vectors": chunk_vecs,
        "query_vectors": query_vecs,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    print(
        f"captured {model} n={len(sample)} dim={dim} "
        f"buckets={payload['buckets']} dropped={len(dropped)} -> {args.out}"
    )
    return 0


def _load_capture(path: str, want_model: str, want_dim: int) -> dict:
    if not os.path.exists(path):
        raise RuntimeError(f"capture file missing: {path} (run capture first)")
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    if payload.get("model") != want_model:
        raise RuntimeError(
            f"{path}: model={payload.get('model')!r}, want {want_model!r} (stale/tampered)"
        )
    if payload.get("dim") != want_dim:
        raise RuntimeError(f"{path}: dim={payload.get('dim')!r}, want {want_dim}")
    n = payload.get("n", 0)
    if n < MIN_N:
        raise RuntimeError(f"{path}: n={n} < {MIN_N} (never auto-pass on thin data)")
    if len(payload.get("chunk_vectors", [])) != n or len(payload.get("query_vectors", [])) != n:
        raise RuntimeError(f"{path}: vector count != n={n} (tampered)")
    if len(payload.get("sample", [])) != n:
        raise RuntimeError(f"{path}: sample count != n={n} (tampered)")
    # Leakage audit: every query must start at/after QUERY_START_MIN.
    for entry in payload["sample"]:
        if int(entry.get("query_start_word", -1)) < QUERY_START_MIN:
            raise RuntimeError(
                f"{path}: query for {entry.get('chunk_id')} starts at word "
                f"{entry.get('query_start_word')} (lexical leakage)"
            )
        if not str(entry.get("query", "")).strip():
            raise RuntimeError(f"{path}: empty query for {entry.get('chunk_id')}")
    return payload


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _recall_at_k(chunk_vecs: list[list[float]], query_vecs: list[list[float]], k: int) -> float:
    """DENSE-ONLY self-retrieval: fraction of queries whose gold chunk ranks top-k."""
    n = len(chunk_vecs)
    norms_c = [math.sqrt(sum(x * x for x in v)) or 1.0 for v in chunk_vecs]
    hits = 0
    for qi, qv in enumerate(query_vecs):
        nq = math.sqrt(sum(x * x for x in qv)) or 1.0
        sims = [
            sum(a * b for a, b in zip(qv, cv)) / (nq * norms_c[ci])
            for ci, cv in enumerate(chunk_vecs)
        ]
        rank = 1 + sum(1 for ci, s in enumerate(sims) if ci != qi and s > sims[qi])
        if rank <= k:
            hits += 1
    return hits / n if n else 0.0


def cmd_score(args: argparse.Namespace) -> int:
    if not args.allow_concurrent:
        up80 = _service_healthy(GRANITE_URL)
        up81 = _service_healthy(BGE_URL)
        if up80 and up81:
            print(
                "FAIL: both :8080 and :8081 answer health -- stop one GGUF "
                "service before scoring (never both at once)",
                file=sys.stderr,
            )
            return FAIL_EXIT
    try:
        granite = _load_capture(args.granite, GRANITE_MODEL, GRANITE_DIM)
        bge = _load_capture(args.bge, BGE_MODEL, BGE_DIM)
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return FAIL_EXIT
    g_ids = [e["chunk_id"] for e in granite["sample"]]
    b_ids = [e["chunk_id"] for e in bge["sample"]]
    if g_ids != b_ids:
        print(
            "FAIL: capture samples differ (granite vs bge chunk_id mismatch)",
            file=sys.stderr,
        )
        return FAIL_EXIT
    n = len(g_ids)
    buckets: dict[str, dict] = {}
    for bucket in sorted({e["bucket"] for e in granite["sample"]}):
        idx = [i for i, e in enumerate(granite["sample"]) if e["bucket"] == bucket]
        if len(idx) < MIN_PER_BUCKET:
            print(
                f"FAIL: bucket {bucket} n={len(idx)} < {MIN_PER_BUCKET}",
                file=sys.stderr,
            )
            return FAIL_EXIT
        g_r = _recall_at_k(
            [granite["chunk_vectors"][i] for i in idx],
            [granite["query_vectors"][i] for i in idx],
            RECALL_K,
        )
        # Cross-model ranking uses each model's own full corpus (same sample).
        b_r = _recall_at_k(
            [bge["chunk_vectors"][i] for i in idx],
            [bge["query_vectors"][i] for i in idx],
            RECALL_K,
        )
        buckets[bucket] = {
            "n": len(idx),
            "granite_recall_at_10": g_r,
            "bge_recall_at_10": b_r,
        }
    g_all = _recall_at_k(granite["chunk_vectors"], granite["query_vectors"], RECALL_K)
    b_all = _recall_at_k(bge["chunk_vectors"], bge["query_vectors"], RECALL_K)
    delta_points = (g_all - b_all) * 100.0
    passed = delta_points >= -TOLERANCE_POINTS
    git_sha = _git_sha()
    artifact = {
        "model": GRANITE_MODEL,
        "dim": GRANITE_DIM,
        "baseline_model": BGE_MODEL,
        "baseline_dim": BGE_DIM,
        "git_sha": git_sha,
        "granite_git_sha": granite.get("git_sha"),
        "bge_git_sha": bge.get("git_sha"),
        "produced_at": _now(),
        "n": n,
        "k": RECALL_K,
        "buckets": buckets,
        "dropped_buckets": granite.get("dropped_buckets", []),
        "recall_at_10_granite": g_all,
        "recall_at_10_bge": b_all,
        "delta_points": delta_points,
        "tolerance_points": TOLERANCE_POINTS,
        "pass": passed,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2)
    print(
        f"n={n} granite_r@{RECALL_K}={g_all:.4f} bge_r@{RECALL_K}={b_all:.4f} "
        f"delta={delta_points:+.2f}pts pass={passed} git_sha={git_sha} -> {args.out}"
    )
    return 0 if passed else FAIL_EXIT


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Granite vs bge-m3 retrieval quality gate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="sample corpus + embed with one model")
    c.add_argument("--model", choices=["granite", "bge"], required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--crispembed-url", default=None)
    c.add_argument("--artifact-dir", default=None)
    c.add_argument("--source-dir", default=None)
    c.add_argument("--per-bucket", type=int, default=40)
    c.add_argument("--expect-dim", type=int, default=None)
    c.set_defaults(func=cmd_capture)
    s = sub.add_parser("score", help="offline dense-only scoring from two caches")
    s.add_argument("--granite", required=True)
    s.add_argument("--bge", required=True)
    s.add_argument("--out", default="quality_gate.json")
    s.add_argument(
        "--allow-concurrent",
        action="store_true",
        help="skip the both-services-up guard (tests only)",
    )
    s.set_defaults(func=cmd_score)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "capture":
        if args.artifact_dir is None:
            args.artifact_dir = _artifact_dir(None)
        if args.source_dir is None:
            args.source_dir = _source_dir(None)
        if args.expect_dim is None:
            args.expect_dim = GRANITE_DIM if args.model == "granite" else BGE_DIM
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
