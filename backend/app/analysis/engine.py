"""Rule-based analysis engine (task 8): facts/issues/gaps/risks + plain language.

Deterministic extraction over stored DocumentSection.normalized_text plus
prior chat (user-role) messages. NO LLM and NO provider call on this path,
so the matter-privacy guard is trivially satisfied; any future LLM
enrichment MUST route through app.llm.check_privacy first (strict +
external + matter evidence -> blocked).

Heuristics are deliberately narrow and documented inline: person/company
name patterns, ISO dates, keyword-triggered obligations/issues, and
same-topic duration conflicts for contradictions. They are best-effort
signals, not legal conclusions — every output carries the span_ref that
produced it so a human can verify.

content_json schema (consumed by task 9 drafts + task 10 frontend; keep
field names stable):
{
  "status": "complete" | "needs-documents",
  "parties": [{"name": str, "kind": "person"|"employer"|"other", "span_ref": ref}],
  "dates": [{"label": "notice date"|"effective date"|"mentioned date", "value": str,
             "span_ref": ref, "needs_review": bool}],
  "obligations": [{"who": str, "what": str, "span_ref": ref}],
  "issues": [{"issue": str, "finding": str, "risk": "High"|"Medium"|"Low",
              "span_refs": [ref, ...]}],
  "gaps": [str, ...],
  "summary_ar": str, "summary_fr": str,
  "glossary_used": [{"term": str, "ar": str, "plain_ar": str, "plain_fr": str}],
  "contradictions": [{"a": ref, "b": ref, "note": str}],
  "knowledge": {"source_statements": [...], "client_assertions": [...],
                "verified_facts": [...], "ai_inferences": [...]}
}
span ref shape: {"document_id": int, "page": int, "span": [int, int]}.
source_statements carry the FULL section text (never truncated) plus title,
span_ref, and needs_review; the gaps "notice date" check requires a date
labeled "notice date" (notice/préavis context), and evidence-date
verified_facts are withheld for needs_review sections.
Knowledge-model rule: source statements (document text), client assertions
(chat user messages), verified facts (structural/file-level facts only),
and AI inferences (heuristic labels) are stored as SEPARATE lists;
contradictions are retained side by side, never merged.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from app.analysis.glossary import match_terms

Risk = Literal["High", "Medium", "Low"]

# Heuristic patterns (English + French keyword roots common in matter files).
# Case-insensitive: the ingestion pipeline lowercases normalized_text
# (faithful_text keeps original casing), so patterns must match lowercase too.
_PERSON_RE = re.compile(
    r"\b(?:Mr|Mrs|Ms|M\.|Mme|Me)\.?\s+([A-ZÀ-Þ][\wÀ-ÿ'’-]+(?:\s+[A-ZÀ-Þ][\wÀ-ÿ'’-]+){0,2})",
    re.IGNORECASE,
)
_COMPANY_RE = re.compile(
    r"([A-ZÀ-Þ][\wÀ-ÿ'’&.-]*(?:\s+[A-ZÀ-Þ][\wÀ-ÿ'’&.-]*){0,3}\s+(?:SARL|SA\b|SAS|LLC|Inc\.?|Ltd\.?))",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/.]\d{1,2}[/.]\d{2,4})\b")
_DURATION_RE = re.compile(r"(\d+\s*(?:months?|mois))", re.IGNORECASE)

EXPECTED_DOC_TYPES: tuple[str, ...] = ("contract", "letter", "correspondence")
PAYSLIPS_EXPECTED = 3


def _ref(document_id: int, page: int, span: list[int]) -> dict[str, Any]:
    return {"document_id": document_id, "page": page, "span": list(span)}


def extract_parties(
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Person titles (Mr/Mme/...) and company suffixes (SARL/SA/...) with spans."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sec in sections:
        text = str(sec.get("text", ""))
        ref = _ref(sec["document_id"], sec["page"], sec["span"])
        for match in _PERSON_RE.finditer(text):
            name = match.group(1).strip()
            if name.lower() not in seen:
                seen.add(name.lower())
                out.append({"name": name, "kind": "person", "span_ref": ref})
        for match in _COMPANY_RE.finditer(text):
            name = match.group(1).strip()
            if name.lower() not in seen:
                seen.add(name.lower())
                out.append({"name": name, "kind": "employer", "span_ref": ref})
    return out


def extract_dates(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ISO/numeric dates with context labels + owning-section review flag.

    Label is 'notice date' near notice-context keywords (notice/préavis),
    'effective date' near effect/partir keywords, else 'mentioned date'.
    Each entry carries needs_review from its owning section so downstream
    consumers can withhold low-confidence dates from verified facts.
    """
    out: list[dict[str, Any]] = []
    for sec in sections:
        text = str(sec.get("text", ""))
        flag = bool(sec.get("needs_review", False))
        ref = _ref(sec["document_id"], sec["page"], sec["span"])
        for match in _DATE_RE.finditer(text):
            window = text[max(0, match.start() - 30) : match.start()].lower()
            if any(k in window for k in ("notice", "préavis", "pré-avis")):
                label = "notice date"
            elif any(k in window for k in ("effect", "partir")):
                label = "effective date"
            else:
                label = "mentioned date"
            out.append(
                {
                    "label": label,
                    "value": match.group(1),
                    "span_ref": ref,
                    "needs_review": flag,
                }
            )
    return out


def extract_obligations(
    sections: list[dict[str, Any]], employer: str = "employer"
) -> list[dict[str, Any]]:
    """Sentences with payment/promise/notice keywords become obligation candidates."""
    triggers = ("will be paid", "shall pay", "must pay", "doit payer", "notice period")
    out: list[dict[str, Any]] = []
    for sec in sections:
        text = str(sec.get("text", ""))
        ref = _ref(sec["document_id"], sec["page"], sec["span"])
        for sentence in re.split(r"(?<=[.!;])\s+", text):
            if any(t in sentence.lower() for t in triggers):
                out.append({"who": employer, "what": sentence.strip(), "span_ref": ref})
    return out


def build_issues(
    sections: list[dict[str, Any]],
    doc_types: list[str],
    obligations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keyword-triggered issues; missing-document issues cite reviewed evidence spans.

    A missing document has no span of its own, so the finding names the
    absence explicitly and span_refs point at the evidence that was reviewed
    (never a fabricated source).
    """
    if not sections:
        return []
    context_refs = [_ref(s["document_id"], s["page"], s["span"]) for s in sections]
    corpus = " ".join(str(s.get("text", "")) for s in sections).lower()

    def _refs_for(keyword_roots: tuple[str, ...]) -> list[dict[str, Any]]:
        hits = [
            _ref(s["document_id"], s["page"], s["span"])
            for s in sections
            if any(k in str(s.get("text", "")).lower() for k in keyword_roots)
        ]
        return hits or context_refs

    issues: list[dict[str, Any]] = []
    if "terminat" in corpus or "licenci" in corpus or "fired" in corpus:
        issues.append(
            {
                "issue": "Employment termination recorded",
                "finding": "Evidence states the employment ended; verify the stated ground and procedure.",
                "risk": "High",
                "span_refs": _refs_for(("terminat", "licenci", "fired")),
            }
        )
    if "notice period" in corpus or "préavis" in corpus or "pré-avis" in corpus:
        issues.append(
            {
                "issue": "Notice period stated",
                "finding": "Evidence states a notice period; verify it was observed and paid.",
                "risk": "Medium",
                "span_refs": _refs_for(("notice period", "préavis", "pré-avis")),
            }
        )
    if (
        "compensat" in corpus
        or "indemnit" in corpus
        or "paid" in corpus
        or "mad" in corpus
    ):
        issues.append(
            {
                "issue": "Final compensation stated",
                "finding": "Evidence mentions a final payment; verify the amount reached the employee.",
                "risk": "Medium",
                "span_refs": _refs_for(("compensat", "indemnit", "paid", "mad")),
            }
        )
    if "contract" not in doc_types:
        issues.append(
            {
                "issue": "Employment contract not on file",
                "finding": "No contract document in this matter; the stated terms cannot be cross-checked.",
                "risk": "High",
                "span_refs": context_refs,
            }
        )
    payslip_count = sum(1 for d in doc_types if d == "payslip")
    if payslip_count < PAYSLIPS_EXPECTED:
        issues.append(
            {
                "issue": "Salary history incomplete",
                "finding": f"Only {payslip_count} payslip(s) on file; {PAYSLIPS_EXPECTED} expected to verify salary claims.",
                "risk": "Medium",
                "span_refs": context_refs,
            }
        )
    if not obligations:
        issues.append(
            {
                "issue": "No explicit obligations detected",
                "finding": "No payment/promise sentence detected; obligations may be in a missing document.",
                "risk": "Low",
                "span_refs": context_refs,
            }
        )
    return issues


def build_gaps(doc_types: list[str], dates: list[dict[str, Any]]) -> list[str]:
    """Missing doc types among contract/letter/payslips/correspondence + notice date."""
    gaps: list[str] = []
    if not doc_types:
        return [
            "No documents on file — upload the employment contract, termination "
            "letter, at least 3 recent payslips, and any correspondence so the "
            "analysis has evidence to work from."
        ]
    for expected in EXPECTED_DOC_TYPES:
        if expected not in doc_types:
            gaps.append(
                f"Missing {expected} — upload the {expected} document to complete the file."
            )
    payslip_count = sum(1 for d in doc_types if d == "payslip")
    if payslip_count < PAYSLIPS_EXPECTED:
        gaps.append(
            f"Missing payslips — {PAYSLIPS_EXPECTED} recent payslips expected, "
            f"only {payslip_count} on file; upload the missing payslips."
        )
    if not any(str(d.get("label", "")) == "notice date" for d in dates):
        gaps.append(
            "Missing notice date — no date in notice context found in the evidence; "
            "provide the document stating when notice started."
        )
    return gaps


def detect_contradictions(
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Same-topic duration conflicts (e.g. two notice periods) kept side by side.

    Groups duration mentions (N months) by topic anchor ('notice period');
    distinct values on the same topic yield one contradiction entry per pair.
    """
    mentions: list[tuple[str, str, dict[str, Any]]] = []
    for sec in sections:
        text = str(sec.get("text", ""))
        lowered = text.lower()
        if "notice period" not in lowered and "préavis" not in lowered:
            continue
        for match in _DURATION_RE.finditer(text):
            mentions.append(
                (
                    "notice-period",
                    match.group(1).lower(),
                    _ref(sec["document_id"], sec["page"], sec["span"]),
                )
            )
    out: list[dict[str, Any]] = []
    for i in range(len(mentions)):
        for j in range(i + 1, len(mentions)):
            topic_i, value_i, ref_i = mentions[i]
            topic_j, value_j, ref_j = mentions[j]
            if topic_i == topic_j and value_i != value_j and ref_i != ref_j:
                out.append(
                    {
                        "a": ref_i,
                        "b": ref_j,
                        "note": (
                            f"Conflicting {topic_i} durations retained side by side: "
                            f"'{value_i}' vs '{value_j}'. Both spans are kept; "
                            "neither is treated as fact."
                        ),
                    }
                )
    return out


def build_summaries(
    sections: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    gaps: list[str],
) -> tuple[str, str, list[dict[str, Any]]]:
    """Plain-language AR/FR summaries with glossary explanations substituted in."""
    corpus = " ".join(str(s.get("text", "")) for s in sections)
    used = match_terms(corpus)
    glossary_used = [
        {
            "term": str(e.get("term", "")),
            "ar": str(e.get("ar", "")),
            "plain_ar": str(e.get("plain_ar", "")),
            "plain_fr": str(e.get("plain_fr", "")),
        }
        for e in used
    ]
    ar_lines = [
        f"تم تحليل {len(sections)} مقطع من وثائق القضية.",
        f"تم رصد {len(issues)} نقطة تحتاج إلى انتباه.",
    ]
    for g in glossary_used:
        ar_lines.append(f"{g['ar']} ({g['term']}): {g['plain_ar']}")
    if gaps:
        ar_lines.append(f"وثائق ناقصة ({len(gaps)}): انظر قائمة النواقص.")
    fr_lines = [
        f"{len(sections)} passage(s) des documents du dossier ont été analysés.",
        f"{len(issues)} point(s) à examiner ont été relevés.",
    ]
    for g in glossary_used:
        fr_lines.append(f"{g['term']} ({g['ar']}) : {g['plain_fr']}")
    if gaps:
        fr_lines.append(
            f"Documents manquants ({len(gaps)}) : voir la liste des lacunes."
        )
    return "\n".join(ar_lines), "\n".join(fr_lines), glossary_used


def build_analysis(
    sections: list[dict[str, Any]],
    doc_types: list[str],
    user_messages: list[str],
    matter_id: int = 0,
) -> dict[str, Any]:
    """Assemble the full content_json payload (pure function; no I/O)."""
    if not sections:
        gaps = build_gaps([], [])
        return {
            "status": "needs-documents",
            "parties": [],
            "dates": [],
            "obligations": [],
            "issues": [],
            "gaps": gaps,
            "summary_ar": "لا توجد وثائق في هذه القضية بعد. أضف وثائق ليتمكن التحليل من العمل.",
            "summary_fr": "Aucun document dans ce dossier pour le moment. Ajoutez des documents pour permettre l'analyse.",
            "glossary_used": [],
            "contradictions": [],
            "knowledge": {
                "source_statements": [],
                "client_assertions": list(user_messages),
                "verified_facts": [],
                "ai_inferences": [],
            },
        }
    parties = extract_parties(sections)
    dates = extract_dates(sections)
    employer = next((p["name"] for p in parties if p["kind"] == "employer"), "employer")
    obligations = extract_obligations(sections, employer=employer)
    issues = build_issues(sections, doc_types, obligations)
    gaps = build_gaps(doc_types, dates)
    contradictions = detect_contradictions(sections)
    summary_ar, summary_fr, glossary_used = build_summaries(sections, issues, gaps)
    source_statements = [
        {
            "text": str(s.get("text", "")),
            "title": str(s.get("title", "")),
            "span_ref": _ref(s["document_id"], s["page"], s["span"]),
            "needs_review": bool(s.get("needs_review", False)),
        }
        for s in sections
    ]
    verified_facts = [
        {"fact": f"Document of type '{t}' on file.", "kind": "file-presence"}
        for t in sorted(set(doc_types))
    ] + [
        {
            "fact": f"Date {d['value']} ({d['label']}) appears in evidence.",
            "kind": "evidence-date",
            "span_ref": d["span_ref"],
        }
        for d in dates
        if not d.get("needs_review", False)
    ]
    ai_inferences = [
        {
            "inference": f"Heuristic issue: {i['issue']} (risk {i['risk']}).",
            "kind": "heuristic-issue",
        }
        for i in issues
    ]
    return {
        "status": "complete",
        "parties": parties,
        "dates": dates,
        "obligations": obligations,
        "issues": issues,
        "gaps": gaps,
        "summary_ar": summary_ar,
        "summary_fr": summary_fr,
        "glossary_used": glossary_used,
        "contradictions": contradictions,
        "knowledge": {
            "source_statements": source_statements,
            "client_assertions": list(user_messages),
            "verified_facts": verified_facts,
            "ai_inferences": ai_inferences,
        },
    }
