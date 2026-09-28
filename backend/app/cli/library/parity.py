"""CLS-pooling parity harness: CrispEmbed-Granite vs HF reference (plan todo 5).

Compares CrispEmbed-served ``granite-embedding-107m`` vectors against the
pinned CPU-only HF reference (CLS pooling + L2 norm, see
``scripts/embedding_reference.py``) over the fixed 20-sentence set in
``app.cli.library.parity_sentences``. Asserts mean cosine >= 0.99 and writes
``parity.json`` with ``{model, dim, git_sha, produced_at, n, mean_cosine,
per_sample}``.

Sequential two-phase usage on this RAM-constrained host (never hold the
served GGUF and the HF reference model in RAM at once)::

    # Phase A (granite service up): cache CrispEmbed vectors
    cd backend && uv run python -m app.cli.library.parity \\
        --write-cache ../.omo/evidence/law-corpus-bulk-ingest/parity-crispembed.json
    # Phase B (service STOPPED): build the HF reference cache
    #   <ref-venv>/bin/python scripts/embedding_reference.py --out <ref.json>
    # Phase C (offline): score from the two caches
    cd backend && uv run python -m app.cli.library.parity \\
        --crispembed-cache <crisp.json> --reference <ref.json> \\
        --out parity.json

Default run (service up + reference cache present): live-captures CrispEmbed
and scores against ``--reference``.

Exit 0 iff mean cosine >= ``--min-cosine`` (default 0.99); exit 2 otherwise,
including on missing/tampered caches (a bad cache can never false-pass).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import subprocess
import sys
import urllib.request

from app.cli.library.parity_sentences import PARITY_SENTENCES, PARITY_SENTENCES_SHA256

MODEL = "granite-embedding-107m"
EXPECTED_DIM = 384
MIN_COSINE = 0.99
FAIL_EXIT = 2


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(here)))
    )


def _default_evidence_dir() -> str:
    return os.path.join(_repo_root(), ".omo", "evidence", "law-corpus-bulk-ingest")


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


def crispembed_vectors(base_url: str, model: str) -> list[list[float]]:
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/embeddings",
        data=json.dumps({"input": list(PARITY_SENTENCES), "model": model}).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.load(resp)
    items = data.get("data", [])
    if len(items) != len(PARITY_SENTENCES):
        raise RuntimeError(
            f"expected {len(PARITY_SENTENCES)} vectors, got {len(items)}"
        )
    return [list(map(float, it["embedding"])) for it in items]


def write_crispembed_cache(path: str, base_url: str, model: str) -> dict:
    vectors = crispembed_vectors(base_url, model)
    payload = {
        "model": model,
        "dim": len(vectors[0]),
        "n": len(vectors),
        "sentences": list(PARITY_SENTENCES),
        "sentences_sha256": PARITY_SENTENCES_SHA256,
        "vectors": vectors,
        "crispembed_url": base_url,
        "produced_at": _now(),
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return payload


def load_vectors_cache(path: str, kind: str) -> dict:
    """Load a vector cache, refusing stale/tampered files loudly."""
    if not os.path.exists(path):
        raise RuntimeError(f"{kind} cache missing: {path}")
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    if payload.get("sentences_sha256") != PARITY_SENTENCES_SHA256:
        raise RuntimeError(f"{kind} cache sentences mismatch (stale/tampered): {path}")
    if payload.get("sentences") != list(PARITY_SENTENCES):
        raise RuntimeError(f"{kind} cache sentences mismatch (stale/tampered): {path}")
    vectors = payload.get("vectors", [])
    if len(vectors) != len(PARITY_SENTENCES):
        raise RuntimeError(
            f"{kind} cache has n={len(vectors)}, want {len(PARITY_SENTENCES)}"
        )
    if any(len(v) != EXPECTED_DIM for v in vectors):
        raise RuntimeError(f"{kind} cache dim mismatch, want {EXPECTED_DIM}")
    return payload


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="CLS-pooling parity gate")
    ap.add_argument("--crispembed-url", default="http://localhost:8080")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--reference", default=None)
    ap.add_argument("--crispembed-cache", default=None)
    ap.add_argument(
        "--write-cache",
        default=None,
        help="capture live CrispEmbed vectors to PATH and exit",
    )
    ap.add_argument("--out", default="parity.json")
    ap.add_argument("--min-cosine", type=float, default=MIN_COSINE)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.write_cache:
        try:
            payload = write_crispembed_cache(
                args.write_cache, args.crispembed_url, args.model
            )
        except Exception as e:
            print(f"FAIL: crispembed capture failed: {e}", file=sys.stderr)
            return FAIL_EXIT
        print(f"captured n={payload['n']} dim={payload['dim']} -> {args.write_cache}")
        return 0

    reference_path = args.reference or os.path.join(
        _default_evidence_dir(), "parity-reference.json"
    )
    try:
        if args.crispembed_cache:
            crisp = load_vectors_cache(args.crispembed_cache, "crispembed")
        else:
            vectors = crispembed_vectors(args.crispembed_url, args.model)
            crisp = {
                "model": args.model,
                "dim": len(vectors[0]),
                "vectors": vectors,
                "crispembed_url": args.crispembed_url,
            }
        ref = load_vectors_cache(reference_path, "reference")
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return FAIL_EXIT

    if crisp["dim"] != EXPECTED_DIM or ref["dim"] != EXPECTED_DIM:
        print(
            f"FAIL: dim mismatch crisp={crisp['dim']} ref={ref['dim']}", file=sys.stderr
        )
        return FAIL_EXIT

    per_sample = [cosine(a, b) for a, b in zip(crisp["vectors"], ref["vectors"])]
    mean_cosine = sum(per_sample) / len(per_sample)
    git_sha = _git_sha()
    parity = {
        "model": args.model,
        "dim": EXPECTED_DIM,
        "git_sha": git_sha,
        "produced_at": _now(),
        "n": len(per_sample),
        "mean_cosine": mean_cosine,
        "per_sample": per_sample,
        "pooling": "cls",
        "reference_pooling": ref.get("pooling"),
        "reference_model": ref.get("model"),
        "reference_revision": ref.get("model_revision"),
        "crispembed_url": crisp.get("crispembed_url", args.crispembed_url),
        "min_cosine": args.min_cosine,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(parity, f, indent=2)
    print(
        f"n={parity['n']} mean_cosine={mean_cosine:.6f} "
        f"min_sample={min(per_sample):.6f} git_sha={git_sha} -> {args.out}"
    )
    if ref.get("pooling") not in (None, "cls"):
        print(
            f"FAIL: reference pooling={ref.get('pooling')!r} is not CLS",
            file=sys.stderr,
        )
        return FAIL_EXIT
    if mean_cosine < args.min_cosine:
        print(
            f"FAIL: mean_cosine {mean_cosine:.6f} < {args.min_cosine}", file=sys.stderr
        )
        return FAIL_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
