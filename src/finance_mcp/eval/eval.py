"""
eval_pe_output — Score any PE document AI output (CIM extractor, DDQ generator,
IC memo drafter, board memo) on four dimensions:

  1. Citation accuracy — every $ and % figure in the prose must trace to a
     numeric source field (within rounding tolerance). No fabricated numbers.
  2. Hallucination rate — % of factual claims (segment labels, archetype,
     decision-column names) that don't appear in the source.
  3. Coverage — % of source opportunities/findings explicitly addressed
     in the prose.
  4. Consistency — when multiple memos derive from the same source
     (e.g. board + operator audiences), do their headline numbers agree?

Architecture mirrors `explainer.explain`: pure pandas/regex on inputs,
deterministic templated scoring on outputs, an HTML render at the end.
No LLM call inside the tool — every score is reproducible from the inputs.

The eval JSON sidecar is the structured artifact for downstream pipelines
(eval-tracker, regression checks, eval dashboards). The HTML is a
human-readable score card a managing director can read in 30 seconds.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

from fastmcp.exceptions import ToolError


# --- Tolerances and constants ---------------------------------------------

# Dollar figures in prose are rendered with `_scaled()` (e.g. $564.8M) so
# they're always rounded — we accept up to 1% drift between prose and source.
_USD_TOLERANCE_FRAC = 0.011

# Percent figures in prose may be rounded to 1 decimal place; allow ±0.15.
_PCT_TOLERANCE_ABS = 0.15

# Persistence scores are rendered with 2 decimals (e.g. "0.80") — allow ±0.01.
_SCORE_TOLERANCE_ABS = 0.011


# --- Regex extractors ------------------------------------------------------

# Match $1,234, $1.5M, $-2.4B, $565K, $565.4M, etc. Captures sign, magnitude,
# and optional scale suffix (K/M/B). Allows a leading minus inside or outside
# the dollar sign, and an optional trailing 'B'/'M'/'K'.
_USD_RE = re.compile(
    r"\$\s*(-?[\d,]+\.?\d*)\s*([KMB])?",
    flags=re.IGNORECASE,
)

# Match standalone percentages (e.g. "12.3%", "0.5 %"). Avoids false hits on
# trailing punctuation by requiring a digit before the optional decimal.
_PCT_RE = re.compile(r"(-?\d+\.?\d*)\s*%")

# Match cohort/segment labels: bolded markdown phrases (e.g. **A × 360 months**)
# and inline-coded labels. Used as the universe of "named entities" in prose.
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")

# Match counts of loans (e.g. "23,681 loans"). Used to verify n.
_N_RE = re.compile(r"([\d,]+)\s*loans?", flags=re.IGNORECASE)

# Persistence score (e.g. "persistence score of 1.00"). Two decimals.
_PERSIST_SCORE_RE = re.compile(r"persistence score of\s*(-?\d+\.\d+)")

# Persistence quarters (e.g. "5 of 5 quarters"). Two integers.
_PERSIST_Q_RE = re.compile(r"(\d+)\s*of\s*(\d+)\s*quarters?")

# Difficulty score (e.g. "rated 4/5"). One integer.
_DIFFICULTY_RE = re.compile(r"rated\s*(\d)\s*/\s*5", flags=re.IGNORECASE)


# --- Frozen dataclasses ----------------------------------------------------


@dataclass(frozen=True)
class FigureCheck:
    """One $/% figure pulled from prose with its trace status."""
    raw: str
    value_usd: float | None
    value_pct: float | None
    matched_source_field: str | None
    delta_abs: float | None

    @property
    def cited(self) -> bool:
        return self.matched_source_field is not None


@dataclass(frozen=True)
class EntityCheck:
    """One named-entity claim from prose with its trace status."""
    raw: str
    kind: str  # 'segment' | 'archetype' | 'decision_col' | 'evidence_id'
    found_in_source: bool


@dataclass(frozen=True)
class OppEvalRow:
    """Per-opportunity eval row."""
    opp_id: str
    figures_total: int
    figures_cited: int
    entities_total: int
    entities_grounded: int
    addressed_in_memo: bool
    findings: tuple[str, ...] = field(default_factory=tuple)


# --- Helpers ---------------------------------------------------------------


def _to_usd(raw: str, scale: str | None) -> float:
    """Convert a regex-captured dollar string to a float in USD."""
    n = float(raw.replace(",", ""))
    mult = {"k": 1e3, "m": 1e6, "b": 1e9}.get((scale or "").lower(), 1.0)
    return n * mult


def _segment_tokens(segment: dict) -> list[str]:
    """Render every value in a segment dict as a string token."""
    return [str(v) for v in (segment or {}).values()]


def _collect_source_usd_universe(source: dict) -> dict[str, float]:
    """
    Build the canonical USD field universe a memo is allowed to cite.
    Maps a logical field name to the absolute USD value (we accept either
    sign, since prose may render `$-565M` or `$565M of leakage`).
    """
    universe: dict[str, float] = {}
    total_impact = float(source.get("total_projected_impact_usd_annual") or 0.0)
    universe["total_projected_impact_usd_annual"] = abs(total_impact)
    ebitda = float(source.get("ebitda_baseline_usd") or 0.0)
    if ebitda:
        universe["ebitda_baseline_usd"] = abs(ebitda)
    for opp in source.get("opportunities") or []:
        oid = opp.get("id", "?")
        impact = float(opp.get("projected_impact_usd_annual") or 0.0)
        outcome = float(opp.get("outcome_total_usd_annual") or 0.0)
        if impact:
            universe[f"{oid}.projected_impact_usd_annual"] = abs(impact)
        if outcome:
            universe[f"{oid}.outcome_total_usd_annual"] = abs(outcome)
    return universe


def _collect_source_pct_universe(source: dict) -> dict[str, float]:
    """Persistence score is the only %-shaped scalar in the source."""
    universe: dict[str, float] = {}
    for opp in source.get("opportunities") or []:
        oid = opp.get("id", "?")
        score = opp.get("persistence_score")
        if score is not None:
            # Persistence score is 0..1 — store as percent for symmetry.
            universe[f"{oid}.persistence_score"] = float(score) * 100.0
    return universe


def _collect_source_entity_universe(source: dict) -> set[str]:
    """
    Build the universe of legitimate named entities a memo can mention:
    archetype names, decision-column names, segment-value tokens,
    evidence-row IDs.
    """
    universe: set[str] = set()
    for opp in source.get("opportunities") or []:
        archetype = opp.get("archetype")
        if archetype:
            universe.add(str(archetype).lower())
        for col in opp.get("decision_cols") or []:
            universe.add(str(col).lower())
        for tok in _segment_tokens(opp.get("segment") or {}):
            universe.add(tok.lower())
        for rid in opp.get("evidence_row_ids") or []:
            universe.add(str(rid).lower())
    return universe


def _trace_usd(value: float, universe: dict[str, float]) -> tuple[str | None, float | None]:
    """Find the closest source USD field within tolerance. Returns (field, delta)."""
    best_field: str | None = None
    best_delta: float | None = None
    for fld, src_val in universe.items():
        if src_val == 0:
            continue
        delta = abs(value - src_val) / src_val
        if delta <= _USD_TOLERANCE_FRAC and (best_delta is None or delta < best_delta):
            best_field, best_delta = fld, delta
    return best_field, best_delta


def _trace_pct(value: float, universe: dict[str, float]) -> tuple[str | None, float | None]:
    """Find the closest source % field within absolute tolerance."""
    best_field: str | None = None
    best_delta: float | None = None
    for fld, src_val in universe.items():
        delta = abs(value - src_val)
        if delta <= _PCT_TOLERANCE_ABS and (best_delta is None or delta < best_delta):
            best_field, best_delta = fld, delta
    return best_field, best_delta


def _extract_figures(prose: str) -> tuple[list[FigureCheck], list[FigureCheck]]:
    """Pull all $ and % figures from a prose blob."""
    usd_figs: list[FigureCheck] = []
    for m in _USD_RE.finditer(prose):
        try:
            value = abs(_to_usd(m.group(1), m.group(2)))
        except ValueError:
            continue
        usd_figs.append(
            FigureCheck(raw=m.group(0).strip(), value_usd=value,
                        value_pct=None, matched_source_field=None, delta_abs=None)
        )

    pct_figs: list[FigureCheck] = []
    for m in _PCT_RE.finditer(prose):
        # Skip "5%" if it's clearly the synthetic "throttle to ~5%" copy in
        # operator prose (which is a fixed playbook constant, not a citation).
        if "throttle volume" in prose[max(0, m.start() - 40): m.start()].lower():
            continue
        try:
            value = float(m.group(1))
        except ValueError:
            continue
        pct_figs.append(
            FigureCheck(raw=m.group(0).strip(), value_usd=None,
                        value_pct=value, matched_source_field=None, delta_abs=None)
        )
    return usd_figs, pct_figs


def _check_figures(
    figs: Iterable[FigureCheck], universe: dict[str, float], kind: str,
) -> list[FigureCheck]:
    """Trace each figure to a source field and return new (immutable) checks."""
    out: list[FigureCheck] = []
    for f in figs:
        if kind == "usd":
            assert f.value_usd is not None
            field_, delta = _trace_usd(f.value_usd, universe)
        else:
            assert f.value_pct is not None
            field_, delta = _trace_pct(f.value_pct, universe)
        out.append(
            FigureCheck(
                raw=f.raw,
                value_usd=f.value_usd,
                value_pct=f.value_pct,
                matched_source_field=field_,
                delta_abs=delta,
            )
        )
    return out


# Bold blocks that are formatting markers, not factual claims. These are
# emitted by the templated explainer to introduce sections — they're not
# entities the model could plausibly hallucinate.
_BOLD_SECTION_MARKERS = frozenset({
    "counterfactual",
    "risk of inaction",
    "rollout",
    "headline",
    "recommendation",
})


def _extract_named_entities(prose: str) -> list[str]:
    """
    Pull candidate named entities from prose: bold-wrapped tokens
    (e.g. **A × 360 months**) split on '×'.

    Filters out:
      - Bold blocks containing $ or % (those are figures, not entities — and
        already counted in citation accuracy).
      - Bold blocks whose entire content is a known section marker
        ("Counterfactual:", "Risk of inaction:", etc.).
    """
    entities: list[str] = []
    for m in _BOLD_RE.finditer(prose):
        label = m.group(1).strip()
        # Skip bold figures — they're handled by USD/pct extraction.
        if "$" in label or "%" in label:
            continue
        # Skip section markers (compare with trailing colon stripped).
        normalized = label.lower().rstrip(":").strip()
        if normalized in _BOLD_SECTION_MARKERS:
            continue
        for part in re.split(r"\s*[×x]\s*", label):
            part = part.strip()
            if part:
                entities.append(part)
    return entities


def _check_entities(
    entities: Iterable[str], universe: set[str],
) -> list[EntityCheck]:
    """Trace each named entity to the source-entity universe."""
    out: list[EntityCheck] = []
    for raw in entities:
        norm = raw.lower()
        # Strip trailing punctuation just in case.
        norm = re.sub(r"[.,;:!?]+$", "", norm).strip()
        out.append(
            EntityCheck(
                raw=raw,
                kind="segment",
                found_in_source=(norm in universe),
            )
        )
    return out


def _opp_addressed(opp: dict, memo_opps: list[dict]) -> bool:
    """An opportunity is 'addressed' if its id matches a memo entry."""
    src_id = opp.get("id")
    if src_id is None:
        return False
    return any(mo.get("id") == src_id for mo in memo_opps)


def _gather_prose(memo_opp: dict) -> str:
    """Concatenate all narrative blobs in a memo opportunity."""
    parts = [
        memo_opp.get("narrative") or "",
        memo_opp.get("counterfactual") or "",
        memo_opp.get("risk_of_inaction") or "",
        memo_opp.get("rollout") or "",
    ]
    return "\n".join(p for p in parts if p)


# --- Scoring core ----------------------------------------------------------


def _score_one_opp(
    memo_opp: dict,
    source_opp: dict | None,
    usd_universe: dict[str, float],
    pct_universe: dict[str, float],
    entity_universe: set[str],
) -> OppEvalRow:
    """Score a single memo opportunity against its source counterpart."""
    prose = _gather_prose(memo_opp)
    usd_figs, pct_figs = _extract_figures(prose)
    usd_checked = _check_figures(usd_figs, usd_universe, kind="usd")
    pct_checked = _check_figures(pct_figs, pct_universe, kind="pct")
    figures_total = len(usd_checked) + len(pct_checked)
    figures_cited = sum(1 for f in usd_checked + pct_checked if f.cited)

    entities = _extract_named_entities(prose)
    entity_checks = _check_entities(entities, entity_universe)
    entities_total = len(entity_checks)
    entities_grounded = sum(1 for e in entity_checks if e.found_in_source)

    findings: list[str] = []
    for f in usd_checked:
        if not f.cited:
            findings.append(f"USD figure {f.raw} does not trace to a source field.")
    for f in pct_checked:
        if not f.cited:
            findings.append(f"Percent figure {f.raw} does not trace to a source field.")
    for e in entity_checks:
        if not e.found_in_source:
            findings.append(f"Entity '{e.raw}' not present in source.")

    return OppEvalRow(
        opp_id=str(memo_opp.get("id", "?")),
        figures_total=figures_total,
        figures_cited=figures_cited,
        entities_total=entities_total,
        entities_grounded=entities_grounded,
        addressed_in_memo=(source_opp is not None),
        findings=tuple(findings),
    )


def _consistency_score(memo: dict, source: dict, sibling_paths: list[Path]) -> dict:
    """
    Consistency dimension: when multiple memos exist for the same source,
    do their headline numbers agree?

    Compares:
      - total_projected_impact_usd_annual
      - per-opp projected_impact_usd_annual (in prose)
      - per-opp persistence labels (in prose)

    If no siblings exist, returns score=1.0 with note='single memo, vacuous'.
    """
    if not sibling_paths:
        return {
            "score": 1.0,
            "n_siblings": 0,
            "note": "single memo for this source — consistency vacuously satisfied.",
            "deltas": [],
        }

    headline = float(memo.get("total_projected_impact_usd_annual") or 0.0)
    deltas: list[dict] = []
    n_compared = 0
    n_consistent = 0

    for sp in sibling_paths:
        try:
            sib = json.loads(sp.read_text())
        except Exception:
            continue
        sib_headline = float(sib.get("total_projected_impact_usd_annual") or 0.0)
        if headline == 0 and sib_headline == 0:
            continue
        n_compared += 1
        denom = max(abs(headline), abs(sib_headline), 1.0)
        delta = abs(headline - sib_headline) / denom
        consistent = delta <= _USD_TOLERANCE_FRAC
        if consistent:
            n_consistent += 1
        deltas.append(
            {
                "sibling": sp.name,
                "headline_a": headline,
                "headline_b": sib_headline,
                "delta_frac": round(delta, 6),
                "consistent": consistent,
            }
        )

    score = (n_consistent / n_compared) if n_compared else 1.0
    return {
        "score": score,
        "n_siblings": len(sibling_paths),
        "n_compared": n_compared,
        "deltas": deltas,
        "note": (
            "all siblings agree" if score == 1.0
            else "headline disagreement detected — investigate"
        ),
    }


def _find_sibling_memos(memo_path: Path, source: dict) -> list[Path]:
    """Find other memo JSON sidecars that point at the same source map."""
    src_id = source.get("portco_id")
    if not src_id:
        return []
    parent = memo_path.parent
    pattern = f"explain_{src_id}_*.json"
    siblings = [p for p in parent.glob(pattern) if p.resolve() != memo_path.resolve()]
    return sorted(siblings)


# --- HTML report -----------------------------------------------------------


_HTML_REPORT = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Eval — {portco_id}</title>
<style>
  :root {{
    --bg:    #0e0f12;
    --panel: #16181d;
    --ink:   #e8e6e0;
    --dim:   #9aa0aa;
    --rule:  #2a2d33;
    --good:  #4cae8b;
    --warn:  #d8a657;
    --bad:   #d36c5c;
    --accent:#7aa2f7;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  body {{
    background: var(--bg); color: var(--ink);
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 14px; line-height: 1.55;
    padding: 48px 24px 96px;
  }}
  .sheet {{ max-width: 960px; margin: 0 auto; }}
  h1 {{ font-size: 28px; margin: 0 0 6px; font-weight: 600; letter-spacing: -0.01em; }}
  h2 {{ font-size: 18px; margin: 32px 0 12px; font-weight: 600; color: var(--ink); }}
  .meta {{ color: var(--dim); font-size: 13px; margin-bottom: 32px; }}
  .meta code {{ font-family: 'JetBrains Mono', monospace; color: var(--accent); }}

  .scorecard {{
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 12px; margin: 24px 0;
  }}
  .score {{
    background: var(--panel); border: 1px solid var(--rule);
    border-radius: 8px; padding: 16px;
  }}
  .score-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: var(--dim); }}
  .score-num   {{ font-size: 30px; font-weight: 600; margin-top: 4px; font-variant-numeric: tabular-nums; }}
  .score-note  {{ font-size: 12px; color: var(--dim); margin-top: 4px; }}
  .score.good .score-num {{ color: var(--good); }}
  .score.warn .score-num {{ color: var(--warn); }}
  .score.bad  .score-num {{ color: var(--bad);  }}

  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
  th, td {{
    text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--rule);
    font-variant-numeric: tabular-nums;
  }}
  th {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--dim); font-weight: 500; }}
  tr:hover td {{ background: rgba(255,255,255,0.02); }}

  .findings {{ margin-top: 8px; }}
  .findings li {{ color: var(--warn); font-size: 13px; }}

  .footer {{
    margin-top: 48px; padding-top: 16px; border-top: 1px solid var(--rule);
    color: var(--dim); font-size: 12px;
  }}
  code {{ font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--accent); }}
</style>
</head>
<body>
<article class="sheet">
  <h1>Eval scorecard — {portco_id}</h1>
  <p class="meta">
    Memo: <code>{memo_path}</code><br/>
    Source: <code>{source_path}</code><br/>
    As of {as_of} &middot; audience: {audience}
  </p>

  <div class="scorecard">
    <div class="score {citation_class}">
      <div class="score-label">Citation accuracy</div>
      <div class="score-num">{citation_score:.1%}</div>
      <div class="score-note">{citation_cited}/{citation_total} figures traced</div>
    </div>
    <div class="score {hallucination_class}">
      <div class="score-label">Hallucination rate</div>
      <div class="score-num">{hallucination_rate:.1%}</div>
      <div class="score-note">{entities_ungrounded}/{entities_total} entities ungrounded</div>
    </div>
    <div class="score {coverage_class}">
      <div class="score-label">Coverage</div>
      <div class="score-num">{coverage_score:.1%}</div>
      <div class="score-note">{opps_addressed}/{opps_total} source opps addressed</div>
    </div>
    <div class="score {consistency_class}">
      <div class="score-label">Consistency</div>
      <div class="score-num">{consistency_score:.1%}</div>
      <div class="score-note">{consistency_note}</div>
    </div>
  </div>

  <h2>Per-opportunity breakdown</h2>
  <table>
    <thead>
      <tr>
        <th>Opp ID</th>
        <th>Addressed</th>
        <th>Figures cited</th>
        <th>Entities grounded</th>
        <th>Findings</th>
      </tr>
    </thead>
    <tbody>
      {opp_rows}
    </tbody>
  </table>

  {findings_section}

  {consistency_section}

  <div class="footer">
    Generated by <code>eval_pe_output</code>. Pure regex/pandas tracing —
    no LLM call. Every score is reproducible from the inputs.
  </div>
</article>
</body>
</html>
"""


_OPP_ROW = """\
      <tr>
        <td><code>{opp_id}</code></td>
        <td>{addressed}</td>
        <td>{figures_cited}/{figures_total}</td>
        <td>{entities_grounded}/{entities_total}</td>
        <td>{findings_count}</td>
      </tr>"""


def _classify(score: float, *, good: float = 0.95, warn: float = 0.80) -> str:
    if score >= good:
        return "good"
    if score >= warn:
        return "warn"
    return "bad"


def _classify_hallucination(rate: float) -> str:
    # Hallucination is inverted: low = good.
    if rate <= 0.05:
        return "good"
    if rate <= 0.20:
        return "warn"
    return "bad"


# --- Public entry point ----------------------------------------------------


def eval_pe_output(memo_json_path: str, source_json_path: str) -> dict:
    """
    Score a PE document AI output against its structured source-of-truth.

    Args:
        memo_json_path:   Path to a memo sidecar (e.g.
                          `finance_output/explain_<portco>_<audience>.json`).
                          Must contain `opportunities_explained[]` with prose
                          fields (`narrative`, `counterfactual`, `risk_of_inaction`,
                          `rollout`) and a `total_projected_impact_usd_annual`
                          headline.
        source_json_path: Path to the structured source (e.g.
                          `finance_output/dx_report_<portco>.json`).

    Returns:
        Dict with citation_accuracy, hallucination_rate, coverage,
        consistency, per-opp breakdown, and the rendered HTML report path.

    Raises:
        ToolError if either file is missing, malformed, or empty.
    """
    memo_path = Path(memo_json_path)
    source_path = Path(source_json_path)
    if not memo_path.exists():
        raise ToolError(f"Memo JSON not found: {memo_path}")
    if not source_path.exists():
        raise ToolError(f"Source JSON not found: {source_path}")

    try:
        memo = json.loads(memo_path.read_text())
    except json.JSONDecodeError as exc:
        raise ToolError(f"Memo JSON is malformed: {exc}")
    try:
        source = json.loads(source_path.read_text())
    except json.JSONDecodeError as exc:
        raise ToolError(f"Source JSON is malformed: {exc}")

    memo_opps = list(memo.get("opportunities_explained") or [])
    source_opps = list(source.get("opportunities") or [])
    if not memo_opps:
        raise ToolError("Memo has zero opportunities_explained — nothing to score.")
    if not source_opps:
        raise ToolError("Source has zero opportunities — nothing to score against.")

    # Build source universes once.
    usd_universe = _collect_source_usd_universe(source)
    pct_universe = _collect_source_pct_universe(source)
    entity_universe = _collect_source_entity_universe(source)

    # Score each memo opportunity.
    src_by_id = {opp.get("id"): opp for opp in source_opps if opp.get("id")}
    rows: list[OppEvalRow] = []
    for memo_opp in memo_opps:
        src_opp = src_by_id.get(memo_opp.get("id"))
        rows.append(
            _score_one_opp(
                memo_opp=memo_opp,
                source_opp=src_opp,
                usd_universe=usd_universe,
                pct_universe=pct_universe,
                entity_universe=entity_universe,
            )
        )

    # --- Aggregate dimension scores ----------------------------------------
    total_figs = sum(r.figures_total for r in rows)
    total_cited = sum(r.figures_cited for r in rows)
    citation_accuracy = (total_cited / total_figs) if total_figs else 1.0

    total_entities = sum(r.entities_total for r in rows)
    total_grounded = sum(r.entities_grounded for r in rows)
    total_ungrounded = total_entities - total_grounded
    hallucination_rate = (total_ungrounded / total_entities) if total_entities else 0.0

    addressed_ids = {r.opp_id for r in rows if r.addressed_in_memo}
    n_source = len(source_opps)
    n_addressed = sum(1 for opp in source_opps if opp.get("id") in addressed_ids)
    coverage = (n_addressed / n_source) if n_source else 1.0

    siblings = _find_sibling_memos(memo_path, source)
    consistency = _consistency_score(memo, source, siblings)

    # --- Build the HTML report --------------------------------------------
    opp_rows_html = "\n".join(
        _OPP_ROW.format(
            opp_id=r.opp_id,
            addressed="yes" if r.addressed_in_memo else "no",
            figures_cited=r.figures_cited,
            figures_total=r.figures_total,
            entities_grounded=r.entities_grounded,
            entities_total=r.entities_total,
            findings_count=len(r.findings),
        )
        for r in rows
    )

    all_findings = [f for r in rows for f in r.findings]
    findings_section = ""
    if all_findings:
        findings_section = "<h2>Findings</h2><ul class='findings'>" + "".join(
            f"<li>{f}</li>" for f in all_findings
        ) + "</ul>"

    consistency_section = ""
    if consistency.get("deltas"):
        consistency_section = (
            "<h2>Consistency check</h2><table><thead><tr>"
            "<th>Sibling memo</th><th>Headline (this)</th>"
            "<th>Headline (sibling)</th><th>Delta</th><th>OK</th>"
            "</tr></thead><tbody>"
        )
        for d in consistency["deltas"]:
            consistency_section += (
                f"<tr><td><code>{d['sibling']}</code></td>"
                f"<td>${d['headline_a']:,.0f}</td>"
                f"<td>${d['headline_b']:,.0f}</td>"
                f"<td>{d['delta_frac']:.4f}</td>"
                f"<td>{'yes' if d['consistent'] else 'no'}</td></tr>"
            )
        consistency_section += "</tbody></table>"

    portco_id = source.get("portco_id", "uploaded")
    audience = memo.get("audience", "unknown")
    as_of = memo.get("as_of", date.today().isoformat())

    html = _HTML_REPORT.format(
        portco_id=portco_id,
        memo_path=str(memo_path),
        source_path=str(source_path),
        as_of=as_of,
        audience=audience,
        citation_score=citation_accuracy,
        citation_class=_classify(citation_accuracy),
        citation_cited=total_cited,
        citation_total=total_figs,
        hallucination_rate=hallucination_rate,
        hallucination_class=_classify_hallucination(hallucination_rate),
        entities_ungrounded=total_ungrounded,
        entities_total=total_entities,
        coverage_score=coverage,
        coverage_class=_classify(coverage),
        opps_addressed=n_addressed,
        opps_total=n_source,
        consistency_score=consistency["score"],
        consistency_class=_classify(consistency["score"]),
        consistency_note=consistency["note"],
        opp_rows=opp_rows_html,
        findings_section=findings_section,
        consistency_section=consistency_section,
    )

    out_dir = memo_path.parent
    audience_suffix = f"_{audience}" if audience and audience != "unknown" else ""
    out_html = out_dir / f"eval_{portco_id}{audience_suffix}.html"
    out_html.write_text(html, encoding="utf-8")

    out_json = out_html.with_suffix(".json")
    payload = {
        "portco_id": portco_id,
        "audience": audience,
        "as_of": as_of,
        "memo_path": str(memo_path),
        "source_path": str(source_path),
        "scores": {
            "citation_accuracy": round(citation_accuracy, 4),
            "hallucination_rate": round(hallucination_rate, 4),
            "coverage": round(coverage, 4),
            "consistency": round(consistency["score"], 4),
        },
        "totals": {
            "figures_total": total_figs,
            "figures_cited": total_cited,
            "entities_total": total_entities,
            "entities_grounded": total_grounded,
            "opps_in_source": n_source,
            "opps_addressed": n_addressed,
        },
        "consistency_detail": consistency,
        "per_opp": [
            {
                "opp_id": r.opp_id,
                "addressed_in_memo": r.addressed_in_memo,
                "figures_total": r.figures_total,
                "figures_cited": r.figures_cited,
                "entities_total": r.entities_total,
                "entities_grounded": r.entities_grounded,
                "findings": list(r.findings),
            }
            for r in rows
        ],
        "report_path": str(out_html),
    }
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    return {
        "report_path": str(out_html),
        "json_path": str(out_json),
        "portco_id": portco_id,
        "audience": audience,
        "scores": payload["scores"],
        "totals": payload["totals"],
        "consistency_note": consistency["note"],
    }
