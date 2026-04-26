"""
Red-flag extractors for SEC 10-K / S-1 / S-4 filings.

Each extractor is deterministic, regex-based, and emits citations (item +
paragraph index + 1-2 sentence excerpt) so a diligence reviewer can verify
every flag in the source text.

Eight flag families ship in this MVP — the categories every PE diligence
team checks first:

  1. customer_concentration   — "X% of [revenues|sales] from a single
                                 customer"
  2. going_concern            — "substantial doubt about the company's
                                 ability to continue as a going concern"
  3. material_weakness        — internal control failures
  4. goodwill_impairment      — write-down of intangible asset value
  5. auditor_change           — accountant change
  6. related_party            — related-party transactions
  7. restatement              — prior period financial restatement
  8. severe_risk_factor       — Item 1A bullets containing escalation
                                 language (material, significant, could harm)

Heuristics deliberately err toward recall — a diligence reviewer prefers
false positives (cheap to dismiss) over false negatives (the deal closes
on a missed flag).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from finance_mcp.cim.parser import ParsedFiling


@dataclass(frozen=True)
class Flag:
    """One red flag with citation."""
    flag_type: str
    severity: str  # "low" | "medium" | "high"
    item: str  # e.g. "1A", "7"
    paragraph_index: int
    excerpt: str  # 1-2 sentences
    rationale: str  # why this is a flag


def _sentences(paragraph: str) -> list[str]:
    """Naive sentence splitter — good enough for citations."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", paragraph.strip())
    return [p.strip() for p in parts if len(p.strip()) > 10]


def _excerpt_around(paragraph: str, match_obj: re.Match) -> str:
    """Return the 1-2 sentences surrounding a regex match."""
    sents = _sentences(paragraph)
    pos = match_obj.start()
    cum = 0
    for i, s in enumerate(sents):
        cum += len(s) + 1
        if cum >= pos:
            # return this sentence + the next if available
            window = sents[i : i + 2]
            joined = " ".join(window)
            return joined[:600] + ("..." if len(joined) > 600 else "")
    return paragraph[:300] + ("..." if len(paragraph) > 300 else "")


# ---------------------------------------------------------------------------
# Individual flag extractors
# ---------------------------------------------------------------------------


_CONCENTRATION_RE = re.compile(
    r"(?i)(?P<pct>\b\d{1,2}(?:\.\d+)?)\s*%\s*"
    r"(?:of\s+(?:our|total)\s+)?"
    r"(?:net\s+)?(?:revenues?|sales|customers?|book|portfolio|orders?|bookings)"
)
_CONC_SCAN_ITEMS = ("1A", "7")


def _flag_customer_concentration(filing: ParsedFiling) -> Iterable[Flag]:
    for item in _CONC_SCAN_ITEMS:
        if item not in filing.paragraphs:
            continue
        for idx, p in enumerate(filing.paragraphs[item]):
            for m in _CONCENTRATION_RE.finditer(p):
                pct = float(m.group("pct"))
                # Only flag concentrations >= 10% — below that is normal
                if pct < 10:
                    continue
                # Skip rate-of-interest lines and similar non-concentration
                lower = p.lower()
                if "interest rate" in lower or "tax rate" in lower:
                    continue
                severity = "high" if pct >= 25 else ("medium" if pct >= 15 else "low")
                yield Flag(
                    flag_type="customer_concentration",
                    severity=severity,
                    item=item,
                    paragraph_index=idx,
                    excerpt=_excerpt_around(p, m),
                    rationale=(
                        f"Concentration disclosure: {pct:.0f}% of "
                        "revenue/customers/portfolio in a single named cohort. "
                        "Diligence: confirm the identity, contract length, "
                        "and renewal terms of the cohort."
                    ),
                )


_GOING_CONCERN_RE = re.compile(
    r"(?i)substantial\s+doubt\s+about\s+(?:the\s+|our\s+)?company['']?s?\s+ability\s+to\s+continue\s+as\s+a\s+going\s+concern"
)


def _flag_going_concern(filing: ParsedFiling) -> Iterable[Flag]:
    for item in ("1A", "7", "9A"):
        if item not in filing.paragraphs:
            continue
        for idx, p in enumerate(filing.paragraphs[item]):
            for m in _GOING_CONCERN_RE.finditer(p):
                yield Flag(
                    flag_type="going_concern",
                    severity="high",
                    item=item,
                    paragraph_index=idx,
                    excerpt=_excerpt_around(p, m),
                    rationale=(
                        "Going-concern language present. The auditor or "
                        "management has flagged liquidity risk to the entity's "
                        "continued operation. Always a high-severity flag."
                    ),
                )


_MATERIAL_WEAKNESS_RE = re.compile(
    r"(?i)material\s+weakness(?:es)?\s+(?:in\s+(?:our\s+)?internal\s+control|over\s+financial\s+reporting)?"
)


def _flag_material_weakness(filing: ParsedFiling) -> Iterable[Flag]:
    for item in ("1A", "9A"):
        if item not in filing.paragraphs:
            continue
        for idx, p in enumerate(filing.paragraphs[item]):
            for m in _MATERIAL_WEAKNESS_RE.finditer(p):
                # Skip negation: "no material weakness" is not a flag
                lower = p.lower()
                if "no material weakness" in lower or "did not identify any material weakness" in lower:
                    continue
                severity = "high" if item == "9A" else "medium"
                yield Flag(
                    flag_type="material_weakness",
                    severity=severity,
                    item=item,
                    paragraph_index=idx,
                    excerpt=_excerpt_around(p, m),
                    rationale=(
                        "Internal-control material weakness disclosed. PE buyers "
                        "discount aggressively for ICFR weakness — confirm "
                        "remediation timing + cost."
                    ),
                )


_GOODWILL_IMPAIRMENT_RE = re.compile(
    r"(?i)goodwill\s+impairment(?:\s+(?:charge|loss|of))?"
)


def _flag_goodwill_impairment(filing: ParsedFiling) -> Iterable[Flag]:
    for item in ("1A", "7", "8"):
        if item not in filing.paragraphs:
            continue
        for idx, p in enumerate(filing.paragraphs[item]):
            for m in _GOODWILL_IMPAIRMENT_RE.finditer(p):
                lower = p.lower()
                if "no goodwill impairment" in lower or "no impairment" in lower:
                    continue
                yield Flag(
                    flag_type="goodwill_impairment",
                    severity="high",
                    item=item,
                    paragraph_index=idx,
                    excerpt=_excerpt_around(p, m),
                    rationale=(
                        "Goodwill impairment charge disclosed. Indicates a prior "
                        "acquisition's modeled value has not materialized — "
                        "diligence the underlying asset and check for "
                        "second-impairment risk."
                    ),
                )


_AUDITOR_CHANGE_RE = re.compile(
    r"(?i)(?:dismiss(?:ed|al)|chang(?:ed|e)\s+in)\s+(?:our\s+)?(?:independent\s+)?(?:registered\s+)?(?:public\s+)?account(?:ant|ing\s+firm)"
)


def _flag_auditor_change(filing: ParsedFiling) -> Iterable[Flag]:
    if "9" not in filing.paragraphs:
        return
    for idx, p in enumerate(filing.paragraphs["9"]):
        for m in _AUDITOR_CHANGE_RE.finditer(p):
            yield Flag(
                flag_type="auditor_change",
                severity="medium",
                item="9",
                paragraph_index=idx,
                excerpt=_excerpt_around(p, m),
                rationale=(
                    "Auditor change in the period. Confirm whether the change "
                    "was firm-driven or company-driven, and review any "
                    "disagreements disclosed."
                ),
            )


_RELATED_PARTY_RE = re.compile(
    r"(?i)related[\-\s]party\s+transactions?"
)


def _flag_related_party(filing: ParsedFiling) -> Iterable[Flag]:
    for item in ("13", "1A"):
        if item not in filing.paragraphs:
            continue
        for idx, p in enumerate(filing.paragraphs[item]):
            for m in _RELATED_PARTY_RE.finditer(p):
                # Avoid TOC-style mentions
                if len(p) < 200:
                    continue
                yield Flag(
                    flag_type="related_party",
                    severity="low",
                    item=item,
                    paragraph_index=idx,
                    excerpt=_excerpt_around(p, m),
                    rationale=(
                        "Related-party transactions disclosed. Standard but "
                        "worth confirming the dollar magnitude + arms-length "
                        "pricing."
                    ),
                )


_RESTATEMENT_RE = re.compile(
    r"(?i)(?:restate(?:d|ment)\s+(?:of\s+)?(?:prior\s+period|previously\s+issued|previously\s+reported))"
)


def _flag_restatement(filing: ParsedFiling) -> Iterable[Flag]:
    for item in ("1A", "7", "8", "9A"):
        if item not in filing.paragraphs:
            continue
        for idx, p in enumerate(filing.paragraphs[item]):
            for m in _RESTATEMENT_RE.finditer(p):
                yield Flag(
                    flag_type="restatement",
                    severity="high",
                    item=item,
                    paragraph_index=idx,
                    excerpt=_excerpt_around(p, m),
                    rationale=(
                        "Restatement of prior-period financials. PE buyers "
                        "treat restatements as a leading indicator of ICFR "
                        "weakness; combine with material-weakness check."
                    ),
                )


# Severe-risk-factor escalation: scans Item 1A for sentences containing
# strong harm language. Recall-tilted by design.
_SEVERITY_LEXICON = re.compile(
    r"(?i)\b(?:material(?:ly)?|substantial(?:ly)?|significant(?:ly)?|"
    r"adverse(?:ly)?|materially\s+harm|materially\s+impact|materially\s+affect)\b"
)


def _flag_severe_risk_factor(filing: ParsedFiling) -> Iterable[Flag]:
    """Scan Item 1A. Each paragraph with multiple severity hits is flagged."""
    if "1A" not in filing.paragraphs:
        return
    for idx, p in enumerate(filing.paragraphs["1A"]):
        hits = list(_SEVERITY_LEXICON.finditer(p))
        if len(hits) < 3:
            continue
        # Skip if it's a short header-style paragraph
        if len(p) < 300:
            continue
        # Use the first match for excerpt anchor
        m = hits[0]
        sev = "medium" if len(hits) < 5 else "high"
        yield Flag(
            flag_type="severe_risk_factor",
            severity=sev,
            item="1A",
            paragraph_index=idx,
            excerpt=_excerpt_around(p, m),
            rationale=(
                f"Risk-factor paragraph with {len(hits)} severity-language "
                "hits ('material', 'substantial', 'adverse', etc.). Stack-rank "
                "against other 1A bullets to identify the operator's "
                "self-disclosed top concerns."
            ),
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_EXTRACTORS = (
    _flag_customer_concentration,
    _flag_going_concern,
    _flag_material_weakness,
    _flag_goodwill_impairment,
    _flag_auditor_change,
    _flag_related_party,
    _flag_restatement,
    _flag_severe_risk_factor,
)


def extract_flags(filing: ParsedFiling) -> list[Flag]:
    """Run every extractor against the parsed filing; return ordered flags."""
    flags: list[Flag] = []
    for fn in _EXTRACTORS:
        flags.extend(fn(filing))
    # Sort by severity then by item
    sev_order = {"high": 0, "medium": 1, "low": 2}
    flags.sort(key=lambda f: (sev_order.get(f.severity, 9), f.item, f.paragraph_index))
    return flags


def summarize_flags(flags: list[Flag]) -> dict:
    """Produce headline counts for the report."""
    by_severity = {"high": 0, "medium": 0, "low": 0}
    by_type: dict[str, int] = {}
    for f in flags:
        by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
        by_type[f.flag_type] = by_type.get(f.flag_type, 0) + 1
    return {
        "total": len(flags),
        "by_severity": by_severity,
        "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
    }
