"""Per-query retrieval trace: dump retrieved chunks to ``backend/data/output/``.

One markdown file per chat query, written fire-and-forget from the chat
route right after the ReAct envelope loop yields its outcome. Never raises:
a dump failure must not break the SSE stream, so errors are logged and
``None`` is returned.

The directory lives under ``data/`` (not the backend root) because only
``app/``, ``alembic/`` and ``data/`` are bind-mounted into the container —
anything else would land invisibly inside container storage.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import BACKEND_ROOT

logger = logging.getLogger(__name__)

OUTPUT_DIR = BACKEND_ROOT / "data" / "output"

_SLUG_MAX: int = 40


def _slugify(query: str) -> str:
    """Filesystem-safe slug from the first words of the query."""
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
    return slug[:_SLUG_MAX].strip("-") or "query"


def _chunk_block(idx: int, hit: dict[str, Any], domain: str) -> str:
    """Render one retrieved hit as a markdown section."""
    lines: list[str] = []
    if domain == "matter":
        ref = hit.get("faithful_ref", "?")
        lines.append(f"### {idx}. {ref}")
        lines.append("")
        meta = (
            f"- document_id: `{hit.get('document_id')}`"
            f" · version: `{hit.get('version_no')}`"
            f" · type: `{hit.get('doc_type')}`"
            f" · page: `{hit.get('page')}`"
            f" · span: `{hit.get('span')}`"
            f" · relevance: `{hit.get('relevance')}`"
        )
        lines.append(meta)
    else:
        ref = hit.get("article_or_section", "?")
        lines.append(f"### {idx}. {ref}")
        lines.append("")
        meta = (
            f"- source: `{hit.get('source')}`"
            f" · version: `{hit.get('version')}`"
            f" · edition: `{hit.get('edition')}`"
            f" · language: `{hit.get('language')}`"
            f" · relevance: `{hit.get('relevance')}`"
        )
        lines.append(meta)
    lines.append("")
    lines.append(str(hit.get("text", "")))
    lines.append("")
    return "\n".join(lines)


def dump_retrieved_chunks(
    query: str,
    matter_hits: list[dict[str, Any]],
    authority_hits: list[dict[str, Any]],
    *,
    matter_id: int | None = None,
    conversation_id: int | None = None,
    tool_rounds: int = 0,
) -> Path | None:
    """Write retrieved chunks for one query to ``backend/data/output/*.md``.

    Returns the file path on success, ``None`` when the dump was skipped
    or failed (failure is logged, never raised).
    """
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        conv = f"_conv{conversation_id}" if conversation_id is not None else ""
        path = OUTPUT_DIR / f"{stamp}{conv}_{_slugify(query)}.md"

        now = datetime.now().isoformat(timespec="seconds")
        parts: list[str] = [
            f"# Retrieval trace — {now}",
            "",
            f"- query: {query}",
            f"- matter_id: `{matter_id}` · conversation_id: `{conversation_id}`"
            f" · tool_rounds: `{tool_rounds}`",
            f"- matter chunks: `{len(matter_hits)}` · authority chunks: `{len(authority_hits)}`",
            "",
            "---",
            "",
            f"## Matter chunks ({len(matter_hits)})",
            "",
        ]
        for i, hit in enumerate(matter_hits, 1):
            parts.append(_chunk_block(i, hit, "matter"))
        parts += [f"## Authority chunks ({len(authority_hits)})", ""]
        for i, hit in enumerate(authority_hits, 1):
            parts.append(_chunk_block(i, hit, "authority"))

        path.write_text("\n".join(parts), encoding="utf-8")
        logger.info("chunk_dump: wrote %s", path)
        return path
    except OSError as exc:
        logger.warning("chunk_dump: skipped (%s: %s)", type(exc).__name__, exc)
        return None
