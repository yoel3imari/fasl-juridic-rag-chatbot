"""Rule-based drafting engine (task 9): grounded templates + review-state labels.

Deterministic assembly over the latest Analysis content_json (issues,
obligations, parties, dates, gaps with span refs) plus authority citations
already stored in the matter (message citations_json). NO provider call on
the template path, so it works with zero LLM providers; the optional polish
path lives in the route and MUST pass app.llm.check_privacy first (strict +
external + matter evidence -> blocked BEFORE any provider call).

Grounding rule: every claim in the generated text carries a span ref from
the analysis or a stored authority citation. Authority article text is
NEVER fabricated — only source/version/article/edition fields already
present in the matter context are cited. Unknown draft_type raises
UnknownDraftTypeError (route maps it to 400).

Label rule (scope guardrail): the literal string "lawyer review" (with a
space) MUST NOT appear in any payload of the acknowledged flow — nor
anywhere else in draft payloads. Status labels are worded to avoid it:
"draft" -> "Draft — ...", "acknowledged" -> "Acknowledged by user — ...",
"lawyer_reviewed" -> "Reviewed by {reviewer} — ...". The ReviewState enum
member name "lawyer_reviewed" (underscores) is the only lawyer-* token and
is a state identifier, not rendered copy.
"""

from __future__ import annotations

from typing import Any

PROVISIONAL_BANNER = "AI-generated draft, not legal advice, verify before use"
PROVISIONAL_BANNER_AR = (
    "مسودة مولّدة بالذكاء الاصطناعي، ليست استشارة قانونية، تحقق قبل الاستعمال"
)
PROVISIONAL_BANNER_FR = (
    "Projet généré par l'IA, ne constitue pas un avis juridique, vérifiez avant usage"
)

DRAFT_TYPES: tuple[str, ...] = ("opinion", "client_email", "demand_letter", "memo")

KNOWN_REVIEW_STATES: frozenset[str] = frozenset(
    {"draft", "acknowledged", "lawyer_reviewed"}
)

AUTHORITY_FIELDS: tuple[str, ...] = (
    "source",
    "version",
    "edition",
    "article_or_section",
    "pub_date",
    "doc_date",
    "language",
)


class UnknownDraftTypeError(ValueError):
    """Raised for draft types outside DRAFT_TYPES (route maps to 400)."""


class IllegalTransitionError(ValueError):
    """Raised for disallowed moves between known states (route maps to 409)."""


def status_label(review_state: str, reviewer: str | None = None) -> str:
    """Human label for a review state; never renders "lawyer review"."""
    if review_state == "acknowledged":
        return "Acknowledged by user — provisional, verify before use"
    if review_state == "lawyer_reviewed":
        return f"Reviewed by {reviewer or 'counsel'} — provisional, verify before use"
    return "Draft — provisional, verify before use"


def resolve_transition(current: str, to_state: str, reviewer: str | None = None) -> str:
    """Validate a review-state move; unknown names raise ValueError (→ 400).

    Allowed: draft → acknowledged, draft → lawyer_reviewed (reviewer
    required), acknowledged → lawyer_reviewed (reviewer required).
    Re-selecting the current state is idempotent. lawyer_reviewed is
    terminal: leaving it raises IllegalTransitionError (→ 409).
    """
    if to_state not in KNOWN_REVIEW_STATES:
        raise ValueError(f"unknown review_state: {to_state!r}")
    if to_state == current:
        return current
    if current == "lawyer_reviewed":
        raise IllegalTransitionError(f"cannot move from lawyer_reviewed to {to_state}")
    if to_state == "lawyer_reviewed" and not (reviewer or "").strip():
        raise ValueError("reviewer is required for lawyer_reviewed")
    if current == "draft" and to_state in ("acknowledged", "lawyer_reviewed"):
        return to_state
    if current == "acknowledged" and to_state == "lawyer_reviewed":
        return to_state
    raise IllegalTransitionError(f"cannot move from {current} to {to_state}")


def _span_key(ref: Any) -> tuple | None:
    if not isinstance(ref, dict):
        return None
    try:
        return (
            int(ref["document_id"]),
            int(ref["page"]),
            tuple(int(x) for x in ref["span"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def collect_matter_citations(content: dict[str, Any]) -> list[dict[str, Any]]:
    """Dedupe span refs from issues/obligations/parties/dates/contradictions."""
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []

    def _add(ref: Any) -> None:
        key = _span_key(ref)
        if key is not None and key not in seen:
            seen.add(key)
            out.append(
                {
                    "domain": "matter",
                    "document_id": key[0],
                    "page": key[1],
                    "span": list(key[2]),
                }
            )

    for issue in content.get("issues", []) or []:
        for ref in issue.get("span_refs", []) or []:
            _add(ref)
    for obl in content.get("obligations", []) or []:
        _add(obl.get("span_ref"))
    for party in content.get("parties", []) or []:
        _add(party.get("span_ref"))
    for date in content.get("dates", []) or []:
        _add(date.get("span_ref"))
    for contra in content.get("contradictions", []) or []:
        _add(contra.get("a"))
        _add(contra.get("b"))
    return out


def collect_authority_citations(
    content: dict[str, Any], stored: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Pass through stored authority citations only — never invent any.

    Sources: authority-domain entries in the matter's message
    citations_json (``stored``) plus any authority list already embedded in
    the analysis content under "authorities"/"authority_citations". Only
    whitelisted fields survive; entries without source + article are
    dropped. Deduped by (source, version, article_or_section).
    """
    candidates: list[Any] = [c for c in stored if isinstance(c, dict)]
    for key in ("authorities", "authority_citations"):
        embedded = content.get(key, []) or []
        if isinstance(embedded, list):
            candidates.extend(c for c in embedded if isinstance(c, dict))
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for cand in candidates:
        if cand.get("domain", "authority") != "authority":
            continue
        source = str(cand.get("source", "")).strip()
        article = str(cand.get("article_or_section", "")).strip()
        if not source or not article:
            continue
        key = (source, str(cand.get("version", "")), article)
        if key in seen:
            continue
        seen.add(key)
        entry: dict[str, Any] = {"domain": "authority"}
        for field in AUTHORITY_FIELDS:
            if cand.get(field) is not None:
                entry[field] = cand[field]
        out.append(entry)
    return out


def format_matter_ref(ref: dict[str, Any]) -> str:
    """Render [matter: doc {id} p.{page} ¶{span}] for inline grounding."""
    return f"[matter: doc {ref['document_id']} p.{ref['page']} ¶{ref['span']}]"


def format_authority_ref(ref: dict[str, Any]) -> str:
    """Render [authority: source version article (edition)] for grounding."""
    return (
        f"[authority: {ref.get('source')} {ref.get('version')} "
        f"{ref.get('article_or_section')} ({ref.get('edition')})]"
    )


def _issues_block(content: dict[str, Any]) -> str:
    lines: list[str] = []
    for issue in content.get("issues", []) or []:
        refs = " ".join(
            format_matter_ref(
                {"document_id": r["document_id"], "page": r["page"], "span": r["span"]}
            )
            for r in issue.get("span_refs", []) or []
            if _span_key(r) is not None
        )
        lines.append(
            f"- {issue.get('issue')}: {issue.get('finding')} "
            f"(risk: {issue.get('risk')}) {refs}".strip()
        )
    return "\n".join(lines) if lines else "- (no issues recorded in the analysis)"


def _obligations_block(content: dict[str, Any]) -> str:
    lines: list[str] = []
    for obl in content.get("obligations", []) or []:
        ref = obl.get("span_ref")
        tag = format_matter_ref(ref) if _span_key(ref) is not None else ""
        lines.append(f"- {obl.get('who')}: {obl.get('what')} {tag}".strip())
    return "\n".join(lines) if lines else "- (no explicit obligations detected)"


def _gaps_block(content: dict[str, Any]) -> str:
    gaps = content.get("gaps", []) or []
    if not gaps:
        return "- (no gaps recorded)"
    return "\n".join(f"- {g}" for g in gaps)


def _authority_block(authority: list[dict[str, Any]]) -> str:
    if not authority:
        return "(none on file — no authority passages stored for this matter)"
    return "\n".join(f"- {format_authority_ref(c)}" for c in authority)


def build_draft(
    draft_type: str,
    content: dict[str, Any],
    *,
    stored_authority: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Assemble (text, citations) for a draft type from analysis content.

    Pure function; no I/O, no provider call. The banner line opens the
    text so the persisted Draft row carries it.
    """
    if draft_type not in DRAFT_TYPES:
        raise UnknownDraftTypeError(f"unknown draft_type: {draft_type!r}")
    matter_cites = collect_matter_citations(content)
    authority_cites = collect_authority_citations(content, stored_authority or [])
    citations: list[dict[str, Any]] = matter_cites + authority_cites
    issues = _issues_block(content)
    obligations = _obligations_block(content)
    gaps = _gaps_block(content)
    authorities = _authority_block(authority_cites)
    summary_ar = str(content.get("summary_ar", "")).strip()
    summary_fr = str(content.get("summary_fr", "")).strip()

    if draft_type == "opinion":
        body = (
            "OPINION (provisional, based solely on the matter file)\n\n"
            f"Summary (AR): {summary_ar}\n\n"
            f"Summary (FR): {summary_fr}\n\n"
            f"Issues identified:\n{issues}\n\n"
            f"Obligations on record:\n{obligations}\n\n"
            f"Authority passages cited:\n{authorities}\n\n"
            f"Open points before any conclusion:\n{gaps}"
        )
    elif draft_type == "client_email":
        body = (
            "Subject: Update on your file (provisional draft)\n\n"
            "Hello,\n\n"
            f"Here is a plain-language update based on your documents:\n{summary_fr}\n\n"
            f"ملخص بالعربية:\n{summary_ar}\n\n"
            f"Points we have spotted:\n{issues}\n\n"
            "To move forward we still need:\n"
            f"{gaps}\n\n"
            "Please confirm the missing documents so the file can be completed."
        )
    elif draft_type == "demand_letter":
        parties = (
            ", ".join(str(p.get("name", "")) for p in content.get("parties", []) or [])
            or "(parties as recorded in the file)"
        )
        body = (
            "DEMAND LETTER (provisional draft — do not send without review)\n\n"
            f"Parties on record: {parties}\n\n"
            f"Facts and obligations relied upon:\n{obligations}\n\n"
            f"Issues supporting this demand:\n{issues}\n\n"
            f"Authority passages cited:\n{authorities}\n\n"
            "Requested next steps (provisional, subject to verification):\n"
            f"{gaps}"
        )
    else:  # memo
        body = (
            "MEMO (provisional, file-internal)\n\n"
            f"FACTS (from matter spans):\n{obligations}\n\n"
            f"ISSUES / RISKS:\n{issues}\n\n"
            f"GAPS (missing documents):\n{gaps}\n\n"
            f"AUTHORITIES ON FILE:\n{authorities}"
        )
    text = f"{PROVISIONAL_BANNER}\n\n{body}"
    return text, citations
