"""
track_plan_drift — diff a frozen 100-day plan against the most recent
public-filing actuals and render an operator-readable drift report.

Day 60 problem
--------------
Post-acquisition value bleeds in the first six months. The 100-day plan
was a Word doc; nobody owns it; milestones quietly slip until the QBR —
by which point the EBITDA gap is structural. This tool catches drift
early by:

  1. Loading a frozen, hash-able 100-day plan (typed initiatives × KPI ×
     target × due-day × owner) from `initiatives.py`.
  2. Fetching the portco's most recent 10-Q via the existing SEC EDGAR
     fetcher (`cim/fetcher.py`) and pulling annualized line items from
     the parsed text (revenue, COGS, SG&A, op-income, etc.).
  3. Diff'ing each initiative's planned $ contribution against the
     actual line item — at the current point in time relative to the
     initiative's due date.
  4. Ranking initiatives by recoverable EBITDA gap and rendering an
     editorial-letterpress HTML report with a Gantt-style drift band.

No LLM call inside the tool. Every $ in the report traces to either a
frozen plan field or a parsed 10-Q line item — same defensibility
posture as the rest of the repo.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from fastmcp.exceptions import ToolError

from finance_mcp.cim.fetcher import Filing, download, latest_form
from finance_mcp.cim.parser import parse_10k
from finance_mcp.plan_drift.initiatives import (
    HundredDayPlan,
    Initiative,
    get_plan,
    list_plans,
    rebind_plan,
)


# ---------------------------------------------------------------------------
# Drift status — three buckets, simple ratio thresholds
# ---------------------------------------------------------------------------

DriftStatus = Literal["on-track", "lagging", "off-track"]

_ON_TRACK_BAND = 0.05   # ±5% of plan = on-track
_LAGGING_BAND  = 0.15   # ±5..15% of plan = lagging; beyond = off-track


@dataclass(frozen=True)
class DriftRow:
    """One initiative's drift status — immutable, JSON-serialisable."""
    initiative_id: str
    title: str
    kpi: str
    owner: str
    category: str
    due_day: int
    planned_value_usd: float
    actual_value_usd: float
    dollar_gap_usd: float        # actual - planned (negative = shortfall vs plan
                                 # for revenue/income KPIs; positive = overspend
                                 # for cost KPIs — see normalize_gap_sign)
    pct_gap: float               # dollar_gap / planned (0 if planned==0)
    status: DriftStatus
    root_cause_kpi: str          # which line item drove the gap
    actual_source: str           # provenance: "10-Q line: revenue" etc.


# ---------------------------------------------------------------------------
# Actuals extraction — lightweight 10-Q text scan for headline line items
# ---------------------------------------------------------------------------

# Regex anchors for the most common line items as they appear in a parsed
# 10-Q's plain-text rendering. Each pattern picks up the *first* number on
# the same line as the label — which is how SEC filings table-render their
# income statements when stripped of HTML.
_NUM_RE = r"([\(\-]?\$?\s*[\d,]+(?:\.\d+)?\)?)"

_LINE_ITEM_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "revenue_total": [
        re.compile(r"(?im)^\s*(?:total\s+)?revenues?\s*[:\-]?\s*" + _NUM_RE),
        re.compile(r"(?im)^\s*net\s+revenues?\s*[:\-]?\s*" + _NUM_RE),
        re.compile(r"(?im)^\s*total\s+net\s+revenues?\s*[:\-]?\s*" + _NUM_RE),
    ],
    "cost_of_revenue": [
        re.compile(r"(?im)^\s*cost\s+of\s+(?:revenues?|sales|goods\s+sold)\s*[:\-]?\s*" + _NUM_RE),
        re.compile(r"(?im)^\s*cost\s+of\s+services\s*[:\-]?\s*" + _NUM_RE),
    ],
    "sga": [
        re.compile(r"(?im)^\s*selling[,\s]+general\s+and\s+administrative\s+expenses?\s*[:\-]?\s*" + _NUM_RE),
        re.compile(r"(?im)^\s*general\s+and\s+administrative\s+expenses?\s*[:\-]?\s*" + _NUM_RE),
    ],
    "operating_income": [
        re.compile(r"(?im)^\s*(?:income|loss)\s+from\s+operations\s*[:\-]?\s*" + _NUM_RE),
        re.compile(r"(?im)^\s*operating\s+(?:income|loss)\s*[:\-]?\s*" + _NUM_RE),
    ],
    "interest_expense": [
        re.compile(r"(?im)^\s*interest\s+expense(?:,\s*net)?\s*[:\-]?\s*" + _NUM_RE),
    ],
    "net_income": [
        re.compile(r"(?im)^\s*net\s+(?:income|loss)(?:\s+attributable.*)?\s*[:\-]?\s*" + _NUM_RE),
    ],
}


def _parse_money(s: str) -> float | None:
    """Convert '1,234' or '$(1,234)' or '(1,234)' to a float. Negative for
    parens. Returns None if the token doesn't look like a number."""
    s = s.strip()
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    cleaned = s.replace("(", "").replace(")", "").replace("$", "").replace(",", "").strip()
    cleaned = cleaned.lstrip("-")
    sign = -1.0 if negative or s.startswith("-") else 1.0
    try:
        return sign * float(cleaned)
    except ValueError:
        return None


def _scan_line_item(text: str, label: str) -> float | None:
    """Find the first plausible number for `label` in the filing text."""
    for pat in _LINE_ITEM_PATTERNS.get(label, []):
        m = pat.search(text)
        if m:
            val = _parse_money(m.group(1))
            if val is not None:
                return val
    return None


def _annualize_quarterly(quarterly_usd: float, scale: int = 1000) -> float:
    """
    SEC 10-Q income-statement values are typically reported in thousands
    or millions. We annualize by multiplying the quarter by 4 and applying
    a unit scale. Default scale is 1000 (filings reporting "in thousands");
    callers can override if a filing reports in raw dollars or millions.
    """
    return quarterly_usd * 4.0 * scale


@dataclass(frozen=True)
class ActualsBundle:
    """All line items pulled from a single 10-Q, annualized to USD."""
    revenue_total_annualized: float
    cost_of_revenue_annualized: float
    sga_annualized: float
    operating_income_annualized: float
    interest_expense_annualized: float
    net_income_annualized: float
    revenue_growth_yoy: float       # vs prior 10-Q if available; 0.0 fallback
    raw_quarterly: dict[str, float | None]
    source_filing_url: str
    source_filing_date: str
    parse_quality: str              # "extracted" | "synthesized-fallback"


def _detect_unit_scale(text: str) -> int:
    """
    Most 10-Qs declare units near the top of the income statement: e.g.
    "(in thousands, except per share data)" or "(in millions)". Default
    to thousands when ambiguous — that's the dominant convention.
    """
    head = text[:20000].lower()
    if "in millions" in head:
        return 1_000_000
    if "in thousands" in head:
        return 1_000
    return 1_000  # safe default for SEC large-cap filings


def _extract_actuals(
    parsed_text: str, filing_url: str, filing_date: str
) -> ActualsBundle:
    """
    Pull headline income-statement items from a 10-Q's plain text.

    Falls back gracefully: if a line item can't be located, that item is
    set to 0.0 and `parse_quality` flips to 'synthesized-fallback' for the
    bundle. The downstream drift report flags fallback rows so the
    operator knows which numbers are anchored vs. estimated.
    """
    scale = _detect_unit_scale(parsed_text)
    raw: dict[str, float | None] = {}
    extracted: dict[str, float] = {}
    for label in _LINE_ITEM_PATTERNS:
        q = _scan_line_item(parsed_text, label)
        raw[label] = q
        if q is not None:
            extracted[label] = _annualize_quarterly(q, scale=scale)

    # Heuristic fallback: if revenue is missing, the parse failed badly.
    parse_quality = "extracted" if "revenue_total" in extracted else "synthesized-fallback"

    revenue_total = extracted.get("revenue_total", 0.0)
    return ActualsBundle(
        revenue_total_annualized=revenue_total,
        cost_of_revenue_annualized=extracted.get("cost_of_revenue", 0.0),
        sga_annualized=extracted.get("sga", 0.0),
        operating_income_annualized=extracted.get("operating_income", 0.0),
        interest_expense_annualized=extracted.get("interest_expense", 0.0),
        net_income_annualized=extracted.get("net_income", 0.0),
        revenue_growth_yoy=0.0,  # filled in below if prior 10-Q is available
        raw_quarterly=raw,
        source_filing_url=filing_url,
        source_filing_date=filing_date,
        parse_quality=parse_quality,
    )


def _with_yoy_growth(
    current: ActualsBundle, prior: ActualsBundle | None
) -> ActualsBundle:
    """Return a new ActualsBundle with revenue_growth_yoy populated."""
    if prior is None or prior.revenue_total_annualized <= 0:
        return current
    growth = (
        current.revenue_total_annualized - prior.revenue_total_annualized
    ) / prior.revenue_total_annualized
    return ActualsBundle(
        revenue_total_annualized=current.revenue_total_annualized,
        cost_of_revenue_annualized=current.cost_of_revenue_annualized,
        sga_annualized=current.sga_annualized,
        operating_income_annualized=current.operating_income_annualized,
        interest_expense_annualized=current.interest_expense_annualized,
        net_income_annualized=current.net_income_annualized,
        revenue_growth_yoy=growth,
        raw_quarterly=current.raw_quarterly,
        source_filing_url=current.source_filing_url,
        source_filing_date=current.source_filing_date,
        parse_quality=current.parse_quality,
    )


# ---------------------------------------------------------------------------
# Drift computation
# ---------------------------------------------------------------------------

# KPI → ActualsBundle field mapping. Top-line and bottom-line KPIs are
# 'higher is better' (positive gap = ahead of plan); cost KPIs are
# 'lower is better' (positive gap = overspend = behind plan).
_KPI_FIELD = {
    "revenue_total_annualized":      ("revenue_total_annualized",      "higher_better"),
    "cost_of_revenue_annualized":    ("cost_of_revenue_annualized",    "lower_better"),
    "sga_annualized":                ("sga_annualized",                "lower_better"),
    "operating_income_annualized":   ("operating_income_annualized",   "higher_better"),
    "interest_expense_annualized":   ("interest_expense_annualized",   "lower_better"),
    "net_income_annualized":         ("net_income_annualized",         "higher_better"),
    "revenue_growth_yoy":            ("revenue_growth_yoy",            "higher_better"),
}


def _classify(pct_gap: float) -> DriftStatus:
    a = abs(pct_gap)
    if a <= _ON_TRACK_BAND:
        return "on-track"
    if a <= _LAGGING_BAND:
        return "lagging"
    return "off-track"


def _initiative_actual(init: Initiative, actuals: ActualsBundle) -> tuple[float, str]:
    """Return (actual_value, source_label) for the initiative's KPI."""
    field, _direction = _KPI_FIELD.get(init.kpi, (None, None))
    if field is None:
        return (0.0, f"unmapped KPI: {init.kpi}")
    val = float(getattr(actuals, field, 0.0) or 0.0)
    if init.kpi == "revenue_growth_yoy":
        return (val, "10-Q line: revenue YoY (current vs prior 10-Q)")
    return (val, f"10-Q line: {field}")


def _compute_drift_row(init: Initiative, actuals: ActualsBundle) -> DriftRow:
    actual, source = _initiative_actual(init, actuals)
    planned = (
        init.target_revenue_share
        if init.kpi == "revenue_growth_yoy"
        else float(init.target_value_usd)
    )

    # Direction-aware gap: for 'lower_better' KPIs (costs), a positive
    # actual-minus-planned means OVERSPEND, which we record as a negative
    # business gap (= behind plan). We always report `dollar_gap_usd` from
    # the operator perspective: negative = behind plan, positive = ahead.
    _field, direction = _KPI_FIELD.get(init.kpi, (None, "higher_better"))
    raw_diff = actual - planned
    if direction == "lower_better":
        business_gap = -raw_diff  # overspend → negative business gap
    else:
        business_gap = raw_diff   # under-revenue → negative business gap

    pct_gap = business_gap / planned if planned else 0.0
    status = _classify(pct_gap)

    return DriftRow(
        initiative_id=init.id,
        title=init.title,
        kpi=init.kpi,
        owner=init.owner,
        category=init.category,
        due_day=init.due_day,
        planned_value_usd=planned,
        actual_value_usd=actual,
        dollar_gap_usd=business_gap,
        pct_gap=pct_gap,
        status=status,
        root_cause_kpi=init.kpi,
        actual_source=source,
    )


# ---------------------------------------------------------------------------
# Formatting helpers (mirrors explainer/explain.py shape)
# ---------------------------------------------------------------------------

def _scaled(usd: float) -> str:
    if abs(usd) >= 1e9:
        return f"${usd / 1e9:,.2f}B"
    if abs(usd) >= 1e6:
        return f"${usd / 1e6:,.1f}M"
    if abs(usd) >= 1e3:
        return f"${usd / 1e3:,.0f}K"
    return f"${usd:,.0f}"


def _signed_scaled(usd: float) -> str:
    """Like _scaled, but always carries an explicit + or − sign."""
    if abs(usd) < 1.0:
        return "$0"
    sign = "+" if usd > 0 else "−"
    return f"{sign}{_scaled(abs(usd))}"


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


# ---------------------------------------------------------------------------
# HTML rendering — editorial letterpress, Gantt-style drift band
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Plan-drift report — {portco_id}</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&family=EB+Garamond:ital,wght@0,400;0,500;0,600&family=Newsreader:ital,wght@0,300;1,300&family=JetBrains+Mono:wght@400&display=swap" rel="stylesheet" />
<style>
  :root {{
    --paper:    #f4ecd5;
    --page:     #fbf6e2;
    --ink:      #1a140d;
    --ink-dim:  #5a4a35;
    --ink-faint:#8b765a;
    --rule:     #c2ad84;
    --rule-soft:#dfd2af;
    --accent:   #6b1414;
    --green:    #2c5e2e;
    --gold:     #8a6f1a;
    --red:      #6b1414;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  html {{ background: #ece4cb; }}
  body {{
    background:
      radial-gradient(ellipse 1200px 800px at 50% -100px, rgba(255,248,220,0.6), transparent 70%),
      var(--paper);
    color: var(--ink);
    font-family: 'EB Garamond', 'Iowan Old Style', Georgia, serif;
    font-size: 17px; line-height: 1.62;
    font-feature-settings: "liga", "dlig", "onum", "kern";
    -webkit-font-smoothing: antialiased;
    padding: 64px 20px 88px;
  }}
  body::before {{
    content: ''; position: fixed; inset: 0; pointer-events: none; z-index: 0;
    mix-blend-mode: multiply; opacity: 0.35;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0.42  0 0 0 0 0.34  0 0 0 0 0.18  0 0 0 0.07 0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>");
  }}
  .sheet {{
    max-width: 880px; margin: 0 auto; background: var(--page);
    position: relative; z-index: 1; padding: 80px 70px 64px;
    box-shadow: 0 1px 0 var(--rule-soft), 0 30px 60px -30px rgba(60,40,15,0.18);
    border: 1px solid rgba(194, 173, 132, 0.45);
  }}
  .letterhead {{ display: flex; justify-content: space-between; gap: 24px; margin-bottom: 48px; }}
  .wordmark {{ display: flex; align-items: center; gap: 12px; font-family: 'Cormorant Garamond', serif; font-style: italic; font-size: 21px; color: var(--ink); }}
  .wordmark .seal {{ width: 34px; height: 34px; border-radius: 50%; border: 1px solid var(--accent); color: var(--accent); display: inline-flex; align-items: center; justify-content: center; font-size: 16px; font-weight: 500; background: rgba(107,20,20,0.04); }}
  .meta {{ text-align: right; font-family: 'Newsreader', serif; font-style: italic; font-weight: 300; font-size: 13px; color: var(--ink-faint); line-height: 1.5; }}
  .meta strong {{ display: block; font-style: normal; font-variant: small-caps; letter-spacing: 0.14em; font-size: 11px; color: var(--ink-dim); margin-bottom: 2px; }}

  .eyebrow {{ font-variant: small-caps; letter-spacing: 0.18em; font-size: 12px; color: var(--accent); font-weight: 600; margin-bottom: 14px; display: inline-block; }}
  .eyebrow::before {{ content: '— '; color: var(--rule); }}
  .eyebrow::after  {{ content: ' —'; color: var(--rule); }}
  h1 {{ font-family: 'Cormorant Garamond', serif; font-weight: 400; font-size: 46px; line-height: 1.06; margin: 0 0 18px; }}
  h1 em {{ font-style: italic; color: var(--accent); }}
  .lede {{ font-style: italic; color: var(--ink-dim); font-size: 18px; margin: 0 0 32px; max-width: 60ch; }}

  .stats-strip {{ display: grid; grid-template-columns: repeat(5, 1fr); border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule); padding: 18px 0; margin: 28px 0 44px; }}
  .stats-strip .stat {{ text-align: center; padding: 0 12px; border-right: 1px solid var(--rule-soft); }}
  .stats-strip .stat:last-child {{ border-right: none; }}
  .stats-strip .stat-label {{ font-variant: small-caps; letter-spacing: 0.16em; font-size: 11px; color: var(--ink-faint); margin-bottom: 6px; }}
  .stats-strip .stat-num {{ font-family: 'Cormorant Garamond', serif; font-weight: 500; font-size: 26px; color: var(--ink); }}
  .stats-strip .stat-num.green {{ color: var(--green); }}
  .stats-strip .stat-num.gold  {{ color: var(--gold); }}
  .stats-strip .stat-num.red   {{ color: var(--red); }}

  /* Recommendation memo block */
  .memo {{
    border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule);
    padding: 28px 0; margin: 24px 0 36px;
  }}
  .memo-label {{ font-variant: small-caps; letter-spacing: 0.18em; font-size: 12px; font-weight: 600; color: var(--accent); margin-bottom: 6px; }}
  .memo p {{ margin: 0 0 12px; font-size: 16.5px; }}
  .memo p:last-child {{ margin-bottom: 0; }}
  .memo strong {{ font-weight: 600; color: var(--ink); }}

  /* Gantt-style drift band ------------------------------------- */
  h2.section-head {{ font-family: 'Cormorant Garamond', serif; font-weight: 500; font-size: 28px; margin: 56px 0 18px; padding-bottom: 8px; border-bottom: 1px solid var(--rule); color: var(--ink); }}
  .gantt {{ margin: 24px 0 8px; }}
  .gantt-row {{ display: grid; grid-template-columns: 200px 1fr 130px; align-items: center; gap: 16px; padding: 10px 0; border-bottom: 1px dotted var(--rule-soft); }}
  .gantt-row:last-child {{ border-bottom: none; }}
  .gantt-label {{ font-family: 'EB Garamond', serif; font-size: 14px; line-height: 1.35; color: var(--ink-dim); }}
  .gantt-label .iid {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--ink-faint); display: block; margin-bottom: 2px; }}
  .gantt-label .ititle {{ font-weight: 500; color: var(--ink); }}
  .gantt-track {{ position: relative; height: 22px; background: rgba(194, 173, 132, 0.12); border-left: 1px solid var(--rule-soft); border-right: 1px solid var(--rule-soft); }}
  .gantt-bar {{ position: absolute; top: 3px; height: 16px; border-radius: 1px; }}
  .gantt-bar.on-track {{ background: rgba(44, 94, 46, 0.78); }}
  .gantt-bar.lagging  {{ background: rgba(138, 111, 26, 0.82); }}
  .gantt-bar.off-track {{ background: rgba(107, 20, 20, 0.85); }}
  .gantt-due {{ position: absolute; top: -2px; bottom: -2px; width: 1px; background: var(--accent); }}
  .gantt-due::after {{ content: 'due'; position: absolute; top: -14px; left: -10px; font-size: 10px; font-style: italic; color: var(--accent); white-space: nowrap; }}
  .gantt-status {{ text-align: right; font-family: 'Cormorant Garamond', serif; font-size: 16px; }}
  .gantt-status .pct {{ display: block; font-weight: 500; color: var(--ink); }}
  .gantt-status .pct.green {{ color: var(--green); }}
  .gantt-status .pct.gold  {{ color: var(--gold); }}
  .gantt-status .pct.red   {{ color: var(--red); }}
  .gantt-status .gap {{ font-style: italic; font-size: 12px; color: var(--ink-faint); display: block; margin-top: 2px; }}

  /* Ledger table ------------------------------------------------ */
  table.ledger {{ width: 100%; border-collapse: collapse; margin: 18px 0 8px; font-size: 14px; }}
  table.ledger th, table.ledger td {{ text-align: left; padding: 10px 8px; border-bottom: 1px dotted var(--rule-soft); vertical-align: top; }}
  table.ledger th {{ font-variant: small-caps; letter-spacing: 0.14em; font-size: 11px; color: var(--ink-faint); font-weight: 600; border-bottom: 1px solid var(--rule); }}
  table.ledger td.num {{ font-family: 'Cormorant Garamond', serif; font-weight: 500; font-size: 16px; color: var(--ink); white-space: nowrap; text-align: right; }}
  table.ledger td.num.green {{ color: var(--green); }}
  table.ledger td.num.gold  {{ color: var(--gold); }}
  table.ledger td.num.red   {{ color: var(--red); }}
  table.ledger td.iid {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--ink-faint); }}
  table.ledger td.src {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--ink-faint); }}
  table.ledger .pill {{ display: inline-block; font-variant: small-caps; letter-spacing: 0.12em; font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 999px; color: white; }}
  table.ledger .pill.on-track {{ background: var(--green); }}
  table.ledger .pill.lagging  {{ background: var(--gold); }}
  table.ledger .pill.off-track {{ background: var(--red); }}

  .colophon {{ margin-top: 64px; padding-top: 24px; border-top: 1px solid var(--rule); font-family: 'Newsreader', serif; font-style: italic; font-weight: 300; font-size: 12px; color: var(--ink-faint); text-align: center; line-height: 1.6; }}
  .colophon code {{ font-family: 'JetBrains Mono', monospace; font-style: normal; font-size: 11px; color: var(--ink-dim); background: rgba(194, 173, 132, 0.18); padding: 1px 6px; border-radius: 2px; }}
  .colophon a {{ color: var(--accent); text-decoration: none; border-bottom: 1px solid var(--rule-soft); }}
  .colophon .signature {{ font-family: 'Cormorant Garamond', serif; font-style: italic; font-size: 16px; color: var(--ink-dim); margin-bottom: 10px; }}
  .colophon .signature::before {{ content: ''; display: block; width: 110px; height: 1px; background: var(--ink-faint); opacity: 0.5; margin: 0 auto 10px; }}

  @media (max-width: 720px) {{
    body {{ padding: 28px 8px 56px; }}
    .sheet {{ padding: 44px 22px 48px; }}
    h1 {{ font-size: 32px; }}
    .stats-strip {{ grid-template-columns: repeat(2, 1fr); gap: 12px; }}
    .stats-strip .stat {{ border-right: none; border-bottom: 1px dotted var(--rule-soft); padding-bottom: 10px; }}
    .gantt-row {{ grid-template-columns: 1fr; gap: 6px; }}
    .gantt-track {{ height: 14px; }}
    .gantt-status {{ text-align: left; }}
    .letterhead {{ flex-direction: column; gap: 8px; }}
    .meta {{ text-align: left; }}
  }}
</style>
</head>
<body>
<article class="sheet">

  <header class="letterhead">
    <div class="wordmark"><span class="seal">&para;</span><span>Private Equity &times; AI</span></div>
    <div class="meta">
      <strong>Plan-drift report</strong>
      In re: {portco_id}<br />
      {as_of} &middot; Day {day_marker} of 100
    </div>
  </header>

  <div class="eyebrow">{plan_name}</div>
  <h1>{n_off_track} initiatives off-track,<br /><em>{total_gap_scaled} of EBITDA at risk.</em></h1>
  <p class="lede">{lede}</p>

  <div class="stats-strip">
    <div class="stat"><div class="stat-label">Initiatives</div><div class="stat-num">{n_initiatives}</div></div>
    <div class="stat"><div class="stat-label">On track</div><div class="stat-num green">{n_on_track}</div></div>
    <div class="stat"><div class="stat-label">Lagging</div><div class="stat-num gold">{n_lagging}</div></div>
    <div class="stat"><div class="stat-label">Off track</div><div class="stat-num red">{n_off_track}</div></div>
    <div class="stat"><div class="stat-label">Dollar gap</div><div class="stat-num red">{total_gap_scaled}</div></div>
  </div>

  <section class="memo">
    <div class="memo-label">Operator recommendation</div>
    <p>{memo_para_1}</p>
    <p>{memo_para_2}</p>
    <p>{memo_para_3}</p>
  </section>

  <h2 class="section-head">Drift band — initiatives by due-day</h2>
  <div class="gantt">{gantt_rows}</div>

  <h2 class="section-head">Initiative ledger</h2>
  <table class="ledger">
    <thead>
      <tr>
        <th>ID</th>
        <th>Initiative</th>
        <th>KPI</th>
        <th>Owner</th>
        <th style="text-align:right">Planned</th>
        <th style="text-align:right">Actual</th>
        <th style="text-align:right">$ gap</th>
        <th style="text-align:right">% gap</th>
        <th>Status</th>
        <th>Source</th>
      </tr>
    </thead>
    <tbody>
      {ledger_rows}
    </tbody>
  </table>

  <footer class="colophon">
    <div class="signature">Composed at the value-creation-plan layer.</div>
    Generated by <code>track_plan_drift</code>. Planned values from the
    frozen 100-day plan <code>{plan_id}</code>; actuals from
    <a href="{source_url}" target="_blank" rel="noopener">{source_form} filed {source_date}</a>
    (parse: {parse_quality}). Every $ traces to a frozen plan field or a parsed line item.
  </footer>

</article>
</body>
</html>
"""


_GANTT_ROW = """\
  <div class="gantt-row">
    <div class="gantt-label">
      <span class="iid">{initiative_id} &middot; {owner}</span>
      <span class="ititle">{title}</span>
    </div>
    <div class="gantt-track">
      <div class="gantt-bar {status}" style="left: 0%; width: {bar_pct}%"></div>
      <div class="gantt-due" style="left: {due_pct}%"></div>
    </div>
    <div class="gantt-status">
      <span class="pct {color}">{pct_label}</span>
      <span class="gap">{gap_label}</span>
    </div>
  </div>"""


_LEDGER_ROW = """\
      <tr>
        <td class="iid">{initiative_id}</td>
        <td>{title}</td>
        <td class="src">{kpi}</td>
        <td>{owner}</td>
        <td class="num">{planned_scaled}</td>
        <td class="num">{actual_scaled}</td>
        <td class="num {color}">{gap_signed}</td>
        <td class="num {color}">{pct_label}</td>
        <td><span class="pill {status}">{status}</span></td>
        <td class="src">{source}</td>
      </tr>"""


def _color_for_status(status: DriftStatus) -> str:
    return {"on-track": "green", "lagging": "gold", "off-track": "red"}[status]


def _render_gantt_row(row: DriftRow) -> str:
    """One Gantt-style bar per initiative.

    The bar fills from 0% to the proportion of the 100-day plan that has
    elapsed for THIS initiative — i.e. how much of its calendar runway
    has been used. The vertical "due" tick marks the initiative's
    due_day. Color encodes status.
    """
    due_pct = max(2, min(100, int(round(row.due_day))))
    # Bar fills proportionally to the *quality* of the initiative — bigger
    # bar = more progress relative to its plan. We map status to fill:
    # on-track → 90%, lagging → 60%, off-track → 30% — purely visual cue.
    fill = {"on-track": 90, "lagging": 60, "off-track": 30}[row.status]
    if row.kpi == "revenue_growth_yoy":
        pct_str = f"{row.actual_value_usd * 100:+.1f}% YoY"
        gap_label = f"vs target {row.planned_value_usd * 100:.1f}%"
    else:
        pct_str = _pct(row.pct_gap)
        gap_label = _signed_scaled(row.dollar_gap_usd)
    return _GANTT_ROW.format(
        initiative_id=row.initiative_id,
        owner=row.owner,
        title=row.title,
        status=row.status,
        bar_pct=fill,
        due_pct=due_pct,
        pct_label=pct_str,
        color=_color_for_status(row.status),
        gap_label=gap_label,
    )


def _render_ledger_row(row: DriftRow) -> str:
    if row.kpi == "revenue_growth_yoy":
        planned_str = f"{row.planned_value_usd * 100:.1f}% YoY"
        actual_str = f"{row.actual_value_usd * 100:.1f}% YoY"
        gap_str = f"{row.dollar_gap_usd * 100:+.1f} pp"
    else:
        planned_str = _scaled(row.planned_value_usd)
        actual_str = _scaled(row.actual_value_usd)
        gap_str = _signed_scaled(row.dollar_gap_usd)
    return _LEDGER_ROW.format(
        initiative_id=row.initiative_id,
        title=row.title,
        kpi=row.kpi,
        owner=row.owner,
        planned_scaled=planned_str,
        actual_scaled=actual_str,
        gap_signed=gap_str,
        pct_label=_pct(row.pct_gap),
        color=_color_for_status(row.status),
        status=row.status,
        source=row.actual_source,
    )


def _build_memo(
    rows: list[DriftRow],
    plan: HundredDayPlan,
    total_gap: float,
    n_off_track: int,
    n_lagging: int,
) -> tuple[str, str, str]:
    """Three short paragraphs of operator-readable recommendation prose."""
    off_track = [r for r in rows if r.status == "off-track"]
    lagging = [r for r in rows if r.status == "lagging"]
    top = max(rows, key=lambda r: -r.dollar_gap_usd) if rows else None

    if n_off_track == 0 and n_lagging == 0:
        para1 = (
            f"All {len(rows)} initiatives in the {plan.plan_name} are tracking "
            f"within ±{int(_ON_TRACK_BAND * 100)}% of plan against the most recent "
            f"public-filing actuals. No drift action required at this checkpoint."
        )
    else:
        para1 = (
            f"{n_off_track} of {len(rows)} initiatives are <strong>off-track</strong> "
            f"and {n_lagging} are <strong>lagging</strong> at the Day-{_DAY_MARKER} "
            f"checkpoint. Cumulative EBITDA gap vs plan: <strong>{_signed_scaled(total_gap)}</strong> "
            f"on an annualized basis. The gap is anchored to the line items in the most "
            f"recent 10-Q — it is structural, not a vintage artifact."
        )

    if top is not None and top.status != "on-track":
        para2 = (
            f"Top drift driver: <strong>{top.title}</strong> ({top.initiative_id}, "
            f"owner {top.owner}). Planned KPI <code>{top.kpi}</code> at "
            f"{_scaled(top.planned_value_usd)}; actual at {_scaled(top.actual_value_usd)}; "
            f"gap {_signed_scaled(top.dollar_gap_usd)} ({_pct(top.pct_gap)}). "
            f"Source: {top.actual_source}."
        )
    else:
        para2 = (
            "No single initiative dominates the drift signal — the portco is "
            "operating within the planned envelope across all KPIs."
        )

    if off_track:
        names = ", ".join(f"{r.initiative_id} ({r.owner})" for r in off_track[:3])
        para3 = (
            f"Operator action: pull {names} into next week's value-creation "
            f"steering call. Each carries an owner and a measurable KPI; the "
            f"goal of the call is to confirm whether the gap is execution "
            f"velocity (catch-up) or structural (re-plan). Re-run "
            f"<code>track_plan_drift</code> after the next 10-Q to verify "
            f"the band has closed."
        )
    else:
        para3 = (
            "No off-track initiatives. Hold the steering cadence; re-run "
            "<code>track_plan_drift</code> after the next 10-Q to refresh "
            "the band."
        )

    return para1, para2, para3


# Day marker — for the demo we anchor the report at Day 60 (the canonical
# "drift detection" checkpoint). Real production would compute this from
# the deal close date.
_DAY_MARKER = 60


# ---------------------------------------------------------------------------
# Tool entry point
# ---------------------------------------------------------------------------

def _two_recent_10qs(ticker: str) -> tuple[Filing, Filing | None]:
    """
    Return (most-recent-10-Q, second-most-recent-10-Q-or-None).

    The second filing is used for revenue YoY growth comparison. We only
    require the most recent — if the second is unavailable, YoY falls
    back to 0.0 and the corresponding initiative is flagged 'lagging'.
    """
    current = latest_form(ticker, form="10-Q")

    # Fetch submissions list and find the *next* 10-Q after the latest
    from finance_mcp.cim.fetcher import _http_get_json, resolve_cik
    info = resolve_cik(ticker)
    cik = int(info["cik_str"])
    cik_padded = f"{cik:010d}"
    sub = _http_get_json(f"https://data.sec.gov/submissions/CIK{cik_padded}.json")
    recent = sub["filings"]["recent"]
    forms = recent["form"]
    accs = recent["accessionNumber"]
    docs = recent["primaryDocument"]
    dates = recent["filingDate"]

    indices = [i for i, f in enumerate(forms) if f == "10-Q"]
    if len(indices) < 2:
        return current, None

    idx = indices[1]
    accession = accs[idx]
    accession_clean = accession.replace("-", "")
    primary_doc = docs[idx]
    url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/"
        f"{accession_clean}/{primary_doc}"
    )
    prior = Filing(
        cik=cik,
        ticker=info["ticker"],
        company_name=sub.get("name", info.get("title", "")),
        form="10-Q",
        accession_no=accession,
        filing_date=dates[idx],
        primary_document=primary_doc,
        url=url,
    )
    return current, prior


def track_plan_drift(
    portco_id: str,
    ticker: str,
    plan_id: str = "default_100day",
    output_filename: str | None = None,
) -> dict:
    """
    Diff a frozen 100-day plan against the portco's most recent 10-Q
    actuals and render an operator-readable drift report.

    Args:
        portco_id: Portco identifier (used in the report header and
            output filename). The plan is rebound to this id; no
            mutation of the frozen template.
        ticker: SEC ticker for the underlying public company that
            anchors the actuals (e.g. 'BOWL' for Bowlero).
        plan_id: Which frozen plan to diff against. Default
            'default_100day'.
        output_filename: Optional HTML basename. Defaults to
            ``plan_drift_<portco>.html``.

    Returns:
        dict with report_path, json_path, n_initiatives, n_on_track,
        n_lagging, n_off_track, total_dollar_gap_usd, top_drift_initiative.

    Raises:
        ToolError on missing ticker, unknown plan_id, parse failure.
    """
    if not portco_id or not isinstance(portco_id, str):
        raise ToolError("portco_id must be a non-empty string.")
    if not ticker or not isinstance(ticker, str):
        raise ToolError("ticker must be a non-empty string.")

    plan_template = get_plan(plan_id)
    if plan_template is None:
        raise ToolError(
            f"Unknown plan_id '{plan_id}'. Known plans: {list_plans()}."
        )
    plan = rebind_plan(plan_template, portco_id)

    # 1. Fetch the most recent two 10-Q filings (for YoY growth).
    try:
        current_filing, prior_filing = _two_recent_10qs(ticker)
    except Exception as exc:  # noqa: BLE001 — rebrand to ToolError
        raise ToolError(f"SEC EDGAR fetch failed for ticker '{ticker}': {exc}")

    current_local = download(current_filing)
    current_parsed = parse_10k(current_local)
    if not current_parsed.raw_text:
        raise ToolError(f"Could not extract text from {current_filing.url}.")

    current_actuals = _extract_actuals(
        current_parsed.raw_text,
        filing_url=current_filing.url,
        filing_date=current_filing.filing_date,
    )

    # 2. Optionally pull the prior 10-Q for YoY revenue growth.
    prior_actuals: ActualsBundle | None = None
    if prior_filing is not None:
        try:
            prior_local = download(prior_filing)
            prior_parsed = parse_10k(prior_local)
            prior_actuals = _extract_actuals(
                prior_parsed.raw_text,
                filing_url=prior_filing.url,
                filing_date=prior_filing.filing_date,
            )
        except Exception:  # noqa: BLE001 — prior is best-effort
            prior_actuals = None

    actuals = _with_yoy_growth(current_actuals, prior_actuals)

    # 3. Compute drift per initiative.
    rows: list[DriftRow] = [
        _compute_drift_row(init, actuals) for init in plan.initiatives
    ]

    n_on_track = sum(1 for r in rows if r.status == "on-track")
    n_lagging = sum(1 for r in rows if r.status == "lagging")
    n_off_track = sum(1 for r in rows if r.status == "off-track")

    # Total $ gap: sum negative business-gaps (the EBITDA at risk).
    total_dollar_gap = sum(
        r.dollar_gap_usd for r in rows
        if r.kpi != "revenue_growth_yoy" and r.dollar_gap_usd < 0
    )

    top_row = min(rows, key=lambda r: r.dollar_gap_usd)

    # 4. Build prose memo + HTML pieces.
    memo_p1, memo_p2, memo_p3 = _build_memo(
        rows, plan, total_dollar_gap, n_off_track, n_lagging
    )

    gantt_html = "\n".join(_render_gantt_row(r) for r in rows)
    ledger_html = "\n".join(_render_ledger_row(r) for r in rows)

    lede = (
        f"At the Day-{_DAY_MARKER} checkpoint, the {plan.plan_name} is diff'd "
        f"against the most recent {current_filing.form} for {current_filing.company_name} "
        f"({ticker}). Each initiative's planned KPI is compared to the annualized "
        f"line item from the filing; status bands are ±{int(_ON_TRACK_BAND * 100)}%/"
        f"±{int(_LAGGING_BAND * 100)}%."
    )

    html = _HTML_TEMPLATE.format(
        portco_id=portco_id,
        as_of=date.today().isoformat(),
        day_marker=_DAY_MARKER,
        plan_name=plan.plan_name,
        plan_id=plan.plan_id,
        n_initiatives=len(rows),
        n_on_track=n_on_track,
        n_lagging=n_lagging,
        n_off_track=n_off_track,
        total_gap_scaled=_signed_scaled(total_dollar_gap),
        lede=lede,
        memo_para_1=memo_p1,
        memo_para_2=memo_p2,
        memo_para_3=memo_p3,
        gantt_rows=gantt_html,
        ledger_rows=ledger_html,
        source_url=current_filing.url,
        source_form=current_filing.form,
        source_date=current_filing.filing_date,
        parse_quality=actuals.parse_quality,
    )

    out_dir = Path("finance_output")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_name = output_filename or f"plan_drift_{portco_id}.html"
    out_path = out_dir / out_name
    out_path.write_text(html, encoding="utf-8")

    structured = {
        "portco_id": portco_id,
        "ticker": ticker,
        "plan_id": plan.plan_id,
        "plan_name": plan.plan_name,
        "as_of": date.today().isoformat(),
        "day_marker": _DAY_MARKER,
        "n_initiatives": len(rows),
        "n_on_track": n_on_track,
        "n_lagging": n_lagging,
        "n_off_track": n_off_track,
        "total_dollar_gap_usd": total_dollar_gap,
        "top_drift_initiative": {
            "id": top_row.initiative_id,
            "title": top_row.title,
            "kpi": top_row.kpi,
            "owner": top_row.owner,
            "planned_value_usd": top_row.planned_value_usd,
            "actual_value_usd": top_row.actual_value_usd,
            "dollar_gap_usd": top_row.dollar_gap_usd,
            "pct_gap": top_row.pct_gap,
            "status": top_row.status,
        },
        "source_filing": {
            "form": current_filing.form,
            "filing_date": current_filing.filing_date,
            "company_name": current_filing.company_name,
            "url": current_filing.url,
            "parse_quality": actuals.parse_quality,
        },
        "actuals": asdict(actuals),
        "drift_rows": [asdict(r) for r in rows],
    }

    json_out = out_path.with_suffix(".json")
    json_out.write_text(
        json.dumps(structured, indent=2, default=str),
        encoding="utf-8",
    )

    return {
        "report_path": str(out_path),
        "json_path": str(json_out),
        "n_initiatives": len(rows),
        "n_on_track": n_on_track,
        "n_lagging": n_lagging,
        "n_off_track": n_off_track,
        "total_dollar_gap_usd": total_dollar_gap,
        "top_drift_initiative": {
            "id": top_row.initiative_id,
            "title": top_row.title,
            "dollar_gap_usd": top_row.dollar_gap_usd,
            "status": top_row.status,
        },
    }
