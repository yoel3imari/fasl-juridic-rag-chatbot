"""HF reference vectors for the parity harness (plan todo 5).

Dev/CI-only script: loads ``ibm-granite/granite-embedding-107m-multilingual``
via transformers on CPU, pools with CLS (per the model's
``1_Pooling/config.json``: ``pooling_mode_cls_token: true``), L2-normalizes,
and writes a cache JSON embedding the exact ``PARITY_SENTENCES`` strings.

Pinned dev-only deps (see ``pyproject.toml`` ``embedding-reference`` extra;
CPU-only torch from the PyTorch CPU index)::

    uv pip install --index-url https://download.pytorch.org/whl/cpu \
        "torch==2.8.0" "transformers==4.57.6" "tokenizers==0.23.2" \
        "huggingface-hub==1.32.0" "safetensors==0.8.0" "numpy==2.5.3"

Usage::

    cd backend && <ref-venv>/bin/python scripts/embedding_reference.py \
        --out ../.omo/evidence/law-corpus-bulk-ingest/parity-reference.json
    # falsification variant (must FAIL the parity gate):
    #   ... --pooling mean --out /tmp/parity-reference-mean.json

Never run concurrently with the CrispEmbed service on this host (RAM).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

HF_MODEL_ID = "ibm-granite/granite-embedding-107m-multilingual"


def _sentences() -> tuple[tuple[str, ...], str]:
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from app.library.parity_sentences import (  # noqa: E402
        PARITY_SENTENCES,
        PARITY_SENTENCES_SHA256,
    )

    return PARITY_SENTENCES, PARITY_SENTENCES_SHA256


def _model_revision(model_id: str) -> str | None:
    try:
        from huggingface_hub import HfApi

        return HfApi().model_info(model_id).sha
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="HF CLS reference for parity gate")
    ap.add_argument("--out", required=True, help="output cache JSON path")
    ap.add_argument("--model", default=HF_MODEL_ID)
    ap.add_argument("--pooling", choices=("cls", "mean"), default="cls")
    ap.add_argument("--batch-size", type=int, default=2)
    ap.add_argument("--revision", default=None)
    args = ap.parse_args()

    sentences, sentences_sha = _sentences()

    import torch
    from transformers import AutoModel, AutoTokenizer

    if str(torch.device("cpu")) != "cpu":  # pragma: no cover - sanity
        raise RuntimeError("CPU-only reference refused non-CPU device")
    torch.set_num_threads(2)

    revision = args.revision or _model_revision(args.model)
    tok = AutoTokenizer.from_pretrained(args.model, revision=revision)
    model = AutoModel.from_pretrained(args.model, revision=revision)
    model.to(torch.device("cpu"))
    model.eval()

    vectors: list[list[float]] = []
    with torch.no_grad():
        for i in range(0, len(sentences), args.batch_size):
            batch = list(sentences[i : i + args.batch_size])
            enc = tok(batch, padding=True, truncation=True, return_tensors="pt")
            out = model(**enc).last_hidden_state
            if args.pooling == "cls":
                pooled = out[:, 0, :]
            else:  # mean pooling: DELIBERATE falsification variant only
                mask = enc["attention_mask"].unsqueeze(-1).expand(out.shape).float()
                pooled = (out * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
            vectors.extend(pooled.cpu().tolist())

    try:
        import transformers as _tf

        tf_version = _tf.__version__
    except Exception:  # pragma: no cover
        tf_version = "unknown"

    payload = {
        "model": args.model,
        "model_revision": revision,
        "pooling": args.pooling,
        "dim": len(vectors[0]),
        "n": len(vectors),
        "sentences": list(sentences),
        "sentences_sha256": sentences_sha,
        "vectors": vectors,
        "torch_version": torch.__version__,
        "transformers_version": tf_version,
        "produced_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    print(
        f"wrote {args.out} n={len(vectors)} dim={len(vectors[0])} pooling={args.pooling}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
