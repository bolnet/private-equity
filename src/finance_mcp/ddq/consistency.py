"""
Cross-answer consistency checker for DDQ responses.

The wedge: ILPA DDQ v2.0 (Q1 2026) added new AI governance / data / risk
sections; funds are answering them inconsistently across vintages and
even within a single response. The tool that catches a contradiction
*before* the LP does — the GP keeps the next allocation.

What a contradiction looks like:
  - Q01 says "12 portcos under AI diligence"; Q02 says "14 portcos"
  - Q06 cites $1.2B of recovery; Q11 cites $1.4B as the AI-attributable total
  - Q09 names LendingCo-EU as in-scope; Q01 inventory does not list it

The checker is deterministic: it extracts dollar figures, integer counts,
percentages, and proper-noun (capitalized multi-word) entity references
from each rendered answer; then compares numerical figures pairwise and
checks that named entities mentioned in any answer also appear in the
fund inventory answer (Q01).

No NLP, no LLM. Pure regex. The whole point is that the contradictions
the checker finds are ones the seller can verify by reading two
paragraphs side-by-side.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


# ---- regex extractors ------------------------------------------------------

# $1.23B, $456.7M, $89K, $1,234, $1234567 — captures the magnitude string
_DOLLAR_PATTERN = re.compile(
    r"\$([0-9][0-9,]*\.?[0-9]*)\s*([BMK]?)",
)

# Bare integer counts: "12 portcos", "5 packs", "23,681 loans". We skip dollars
# (already handled) and percentages (handled separately).
_COUNT_PATTERN = re.compile(
    r"\b([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)\s+"
    r"(portcos?|packs?|loans?|memos?|opportunities|opportunity|findings?|"
    r"claims?|companies|company|portfolio companies?|sidecars?|cohorts?|"
    r"quarters?|rows?|systems?)",
    re.IGNORECASE,
)

# 67%, 50.0%
_PERCENT_PATTERN = re.compile(r"\b([0-9]+\.?[0-9]*)\s*%")

# Named-entity heuristic: 1-3 capitalized words optionally separated by spaces,
# hyphens, or underscores — matches portco_ids like "MortgageCo", "HMDA_GA",
# "LendingCo-EU", "DCMortgage". This is intentionally narrow to avoid false
# positives on sentence-initial caps.
_ENTITY_PATTERN = re.compile(
    r"\b("
    r"[A-Z][a-zA-Z]*[A-Z][a-zA-Z0-9_-]*"      # CamelCase: MortgageCo, DCMortgage
    r"|[A-Z]{2,}_[A-Z0-9]{1,5}"                # ALLCAPS_XX: HMDA_GA
    r"|[A-Z][a-zA-Z]+-[A-Z]{2}"                # Hyphen-CC: LendingCo-EU
    r")\b"
)


# ---- structured outputs ----------------------------------------------------


@dataclass(frozen=True)
class ExtractedFigure:
    """A numeric figure extracted from an answer, normalized to a base unit.

    For dollars, value is in raw USD. For counts, value is the integer.
    For percentages, value is the percent (0-100).
    """

    kind: str          # 'dollar' | 'count' | 'percent'
    value: float
    raw: str           # the literal substring it was lifted from
    unit: str          # 'usd' | the unit word ('portcos', 'loans', ...) | '%'


@dataclass(frozen=True)
class ConsistencyFlag:
    """A pairwise contradiction between two answers."""

    flag_type: str            # 'numeric_mismatch' | 'entity_orphan'
    severity: str             # 'high' | 'medium' | 'low'
    question_ids: tuple[str, ...]
    description: str
    evidence: tuple[str, ...] = field(default_factory=tuple)


# ---- extraction ------------------------------------------------------------


def _scale_dollar(magnitude: str, suffix: str) -> float:
    """Convert '$1.23B' / '$456M' / '$1,234' → float USD."""
    raw = float(magnitude.replace(",", ""))
    if suffix.upper() == "B":
        return raw * 1e9
    if suffix.upper() == "M":
        return raw * 1e6
    if suffix.upper() == "K":
        return raw * 1e3
    return raw


def extract_figures(text: str) -> list[ExtractedFigure]:
    """Extract every dollar / count / percent figure from a free-text answer.

    Order of operations matters: dollars first (because they contain digits
    that could otherwise be mis-classified as bare counts), then percents,
    then bare counts.
    """
    if not text:
        return []

    figures: list[ExtractedFigure] = []
    consumed_spans: list[tuple[int, int]] = []

    for m in _DOLLAR_PATTERN.finditer(text):
        magnitude, suffix = m.group(1), m.group(2)
        try:
            value = _scale_dollar(magnitude, suffix)
        except ValueError:
            continue
        figures.append(
            ExtractedFigure(
                kind="dollar",
                value=value,
                raw=m.group(0),
                unit="usd",
            )
        )
        consumed_spans.append((m.start(), m.end()))

    for m in _PERCENT_PATTERN.finditer(text):
        if any(s <= m.start() < e for s, e in consumed_spans):
            continue
        try:
            value = float(m.group(1))
        except ValueError:
            continue
        figures.append(
            ExtractedFigure(
                kind="percent",
                value=value,
                raw=m.group(0),
                unit="%",
            )
        )
        consumed_spans.append((m.start(), m.end()))

    for m in _COUNT_PATTERN.finditer(text):
        if any(s <= m.start() < e for s, e in consumed_spans):
            continue
        try:
            value = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        figures.append(
            ExtractedFigure(
                kind="count",
                value=value,
                raw=m.group(0),
                unit=m.group(2).lower(),
            )
        )

    return figures


def extract_entities(text: str) -> set[str]:
    """Extract candidate proper-noun entities (portco_ids etc.) from text."""
    if not text:
        return set()
    return {m.group(1) for m in _ENTITY_PATTERN.finditer(text)}


# ---- pairwise comparison ---------------------------------------------------


def _is_dollar_mismatch(a: ExtractedFigure, b: ExtractedFigure, tol: float) -> bool:
    """Two dollar figures conflict if their relative gap exceeds `tol`."""
    if a.kind != "dollar" or b.kind != "dollar":
        return False
    largest = max(abs(a.value), abs(b.value))
    if largest == 0:
        return False
    return abs(a.value - b.value) / largest > tol


def _is_count_mismatch(a: ExtractedFigure, b: ExtractedFigure) -> bool:
    """Two integer counts on the *same unit* conflict if they differ at all."""
    if a.kind != "count" or b.kind != "count":
        return False
    if a.unit.rstrip("s") != b.unit.rstrip("s"):
        return False
    return int(a.value) != int(b.value)


def _check_numeric_pair(
    answer_a: dict, answer_b: dict, dollar_tolerance: float
) -> list[ConsistencyFlag]:
    """Compare numeric figures between two answers; flag clear contradictions."""
    flags: list[ConsistencyFlag] = []
    figures_a = answer_a["figures"]
    figures_b = answer_b["figures"]
    qid_a = answer_a["question_id"]
    qid_b = answer_b["question_id"]

    # Count contradictions on shared unit (e.g., "portcos" in Q01 vs Q09)
    for fa in figures_a:
        for fb in figures_b:
            if _is_count_mismatch(fa, fb):
                flags.append(
                    ConsistencyFlag(
                        flag_type="numeric_mismatch",
                        severity="high",
                        question_ids=(qid_a, qid_b),
                        description=(
                            f"Count of '{fa.unit}' disagrees between "
                            f"{qid_a} ({int(fa.value)}) and "
                            f"{qid_b} ({int(fb.value)})."
                        ),
                        evidence=(fa.raw, fb.raw),
                    )
                )

    # Dollar headline contradictions (large gap on the *same scale*)
    big_a = [f for f in figures_a if f.kind == "dollar" and f.value >= 1e6]
    big_b = [f for f in figures_b if f.kind == "dollar" and f.value >= 1e6]
    for fa in big_a:
        for fb in big_b:
            if _is_dollar_mismatch(fa, fb, dollar_tolerance):
                # only flag if the *intent* could be the same headline:
                # both must be in the same order of magnitude band
                if abs(fa.value - fb.value) / max(fa.value, fb.value) <= 0.5:
                    flags.append(
                        ConsistencyFlag(
                            flag_type="numeric_mismatch",
                            severity="medium",
                            question_ids=(qid_a, qid_b),
                            description=(
                                f"Headline $ figure differs across "
                                f"{qid_a} ({fa.raw}) and {qid_b} ({fb.raw}) "
                                f"by more than {int(dollar_tolerance * 100)}%."
                            ),
                            evidence=(fa.raw, fb.raw),
                        )
                    )
    return flags


def _check_entity_orphans(
    answers: list[dict], inventory_qid: str
) -> list[ConsistencyFlag]:
    """Any entity named in another answer should also appear in the inventory."""
    inventory = next(
        (a for a in answers if a["question_id"] == inventory_qid), None
    )
    if inventory is None:
        return []

    inventory_entities = inventory["entities"]
    flags: list[ConsistencyFlag] = []
    for ans in answers:
        if ans["question_id"] == inventory_qid:
            continue
        orphans = ans["entities"] - inventory_entities
        # Drop generic capitalization noise that isn't a portco_id-shaped token
        orphans = {e for e in orphans if "_" in e or "-" in e or _is_camel(e)}
        for orphan in sorted(orphans):
            flags.append(
                ConsistencyFlag(
                    flag_type="entity_orphan",
                    severity="medium",
                    question_ids=(inventory_qid, ans["question_id"]),
                    description=(
                        f"Entity '{orphan}' is referenced in "
                        f"{ans['question_id']} but does not appear in the "
                        f"fund inventory answer ({inventory_qid})."
                    ),
                    evidence=(orphan,),
                )
            )
    return flags


def _is_camel(token: str) -> bool:
    """True if `token` has at least one internal uppercase (e.g., MortgageCo)."""
    if len(token) < 2:
        return False
    return any(c.isupper() for c in token[1:])


# ---- public entry point ----------------------------------------------------


def check_consistency(
    answers: Iterable[dict],
    *,
    inventory_qid: str = "Q01_GOV_INVENTORY",
    dollar_tolerance: float = 0.05,
) -> list[ConsistencyFlag]:
    """
    Run the cross-answer consistency checker over a set of rendered DDQ answers.

    Args:
        answers: Iterable of dicts with keys
            {question_id, category, text, figures, entities}.
        inventory_qid: The question id treated as the canonical fund
            inventory; entities mentioned in other answers should appear
            here.
        dollar_tolerance: Relative gap above which two dollar figures in
            the same magnitude band are flagged as contradictory.

    Returns:
        A list of ConsistencyFlag records. Empty list = no contradictions
        found, which is itself a publishable finding.
    """
    answer_list = list(answers)
    flags: list[ConsistencyFlag] = []

    # Pairwise numeric comparison
    for i, a in enumerate(answer_list):
        for b in answer_list[i + 1 :]:
            flags.extend(_check_numeric_pair(a, b, dollar_tolerance))

    # Entity-orphan check against the inventory question
    flags.extend(_check_entity_orphans(answer_list, inventory_qid))

    # Deduplicate near-identical flags (same description string)
    seen: set[str] = set()
    unique: list[ConsistencyFlag] = []
    for f in flags:
        key = f.description
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)

    return unique
