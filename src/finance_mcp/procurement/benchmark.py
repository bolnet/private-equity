"""
benchmark_vendors — Apollo-style cross-portco vendor price benchmarking.

The mechanic Apollo uses across its 50-person procurement team: line up the
same SKU across N portcos, surface the price gap between the best and worst
buyer, then move every portco onto the best price. This tool productizes
that mechanic for funds without Apollo's headcount, demoed against the
USAspending public dataset.

Architecture mirrors the rest of the toolchain:

  * Pure pandas + stdlib on the inputs — deterministic, no ML.
  * Frozen DTOs (dataclass(frozen=True)) for analysis outputs.
  * Editorial-letterpress HTML render at the end (Cormorant Garamond +
    EB Garamond + paper cream — same typography vocabulary as the
    board memo and the normalize-portco digest).

Inputs: a PSC code (the federal "service-line SKU" classifier), a fiscal
year, and a record cap. Outputs (under finance_output/, basename driven
by `output_filename`):

  * report.html    — editorial-letterpress savings memo with a rank table
  * report.json    — sidecar with per-agency recommendations + the full
                     vendor × agency price matrix
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import pandas as pd
from fastmcp.exceptions import ToolError

from finance_mcp.procurement.fetcher import fetch_awards

# ---------------------------------------------------------------------------
# Constants — no magic numbers buried in the analysis logic
# ---------------------------------------------------------------------------

_MIN_AGENCIES_FOR_BENCHMARK: int = 2          # need at least 2 buyers to compare
_MIN_VENDORS_FOR_BENCHMARK: int = 2           # need at least 2 sellers in the cohort
_TOP_OPPORTUNITIES_IN_HTML: int = 15          # rank table cap
_MIN_AWARD_AMOUNT_USD: float = 1.0            # drop $0 / negative awards
_MAX_RECORDS_HARD_CAP: int = 50_000           # safety rail on caller input

# PSC -> human label. Covers the categories the demo prompt suggests
# (IT services, IT support, janitorial, office supplies). Falls back to
# the raw PSC code if not in the table.
_PSC_LABELS: dict[str, str] = {
    "D310": "IT and Telecom — IT Support Services",
    "D316": "IT and Telecom — Telecommunications Network Management",
    "D318": "IT and Telecom — Integrated Hardware/Software Solutions",
    "R425": "Engineering and Technical Services",
    "S201": "Custodial / Janitorial Services",
    "7510": "Office Supplies",
    "7520": "Office Devices and Accessories",
    "7530": "Stationery and Record Forms",
}


# ---------------------------------------------------------------------------
# Frozen DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgencyOpportunity:
    """One agency's savings opportunity if it moved to the best peer price."""

    agency: str                       # awarding agency name (the "portco")
    current_avg_price_per_award: float
    best_peer_price_per_award: float
    n_awards_in_cohort: int
    spend_in_cohort_usd: float
    savings_if_matched_best_usd: float
    best_peer_agency: str             # which agency is buying cheaper


@dataclass(frozen=True)
class VendorSpread:
    """Cross-agency price spread for one vendor."""

    vendor: str
    n_agencies: int
    n_awards: int
    min_avg_price: float
    max_avg_price: float
    spread_ratio: float               # max / min — 1.0 means no spread
    total_spend_usd: float


# ---------------------------------------------------------------------------
# Step 1 — normalize the API records into a tidy frame
# ---------------------------------------------------------------------------


def _records_to_frame(records: list[dict]) -> pd.DataFrame:
    """Convert raw USAspending records into a tidy contract-level frame.

    Drops rows with non-positive award amounts or missing agency / vendor.
    """
    if not records:
        return pd.DataFrame(
            columns=[
                "award_id",
                "agency",
                "sub_agency",
                "vendor",
                "award_amount",
                "psc",
                "naics",
                "description",
                "start_date",
                "end_date",
            ]
        )

    rows = []
    for rec in records:
        amount_raw = rec.get("Award Amount")
        try:
            amount = float(amount_raw) if amount_raw is not None else 0.0
        except (TypeError, ValueError):
            amount = 0.0
        rows.append(
            {
                "award_id": rec.get("Award ID") or rec.get("generated_internal_id"),
                "agency": (rec.get("Awarding Agency") or "").strip(),
                "sub_agency": (rec.get("Awarding Sub Agency") or "").strip(),
                "vendor": (rec.get("Recipient Name") or "").strip(),
                "award_amount": amount,
                "psc": rec.get("PSC") or "",
                "naics": rec.get("NAICS") or "",
                "description": rec.get("Description") or "",
                "start_date": rec.get("Start Date") or "",
                "end_date": rec.get("End Date") or "",
            }
        )

    df = pd.DataFrame(rows)
    df = df[df["award_amount"] >= _MIN_AWARD_AMOUNT_USD]
    df = df[df["agency"].astype(bool) & df["vendor"].astype(bool)]
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Step 2 — build the vendor × agency price matrix
# ---------------------------------------------------------------------------


def _build_price_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Vendor × agency mean-award-price matrix.

    Cell value = mean award amount for (vendor, agency) over the period.
    NaN for (vendor, agency) pairs with no awards.
    """
    if df.empty:
        return pd.DataFrame()
    grouped = (
        df.groupby(["vendor", "agency"], as_index=False)["award_amount"]
        .mean()
        .rename(columns={"award_amount": "avg_price"})
    )
    matrix = grouped.pivot(index="vendor", columns="agency", values="avg_price")
    return matrix.sort_index()


# ---------------------------------------------------------------------------
# Step 3 — vendor-level spread (which vendor charges the widest range?)
# ---------------------------------------------------------------------------


def _vendor_spreads(df: pd.DataFrame) -> tuple[VendorSpread, ...]:
    """For each vendor with >=2 agency customers, compute price spread."""
    if df.empty:
        return ()

    by_vendor_agency = (
        df.groupby(["vendor", "agency"])
        .agg(avg_price=("award_amount", "mean"), n_awards=("award_amount", "size"),
             total_spend=("award_amount", "sum"))
        .reset_index()
    )

    spreads: list[VendorSpread] = []
    for vendor, group in by_vendor_agency.groupby("vendor"):
        if len(group) < _MIN_AGENCIES_FOR_BENCHMARK:
            continue
        prices = group["avg_price"].to_numpy()
        min_p = float(prices.min())
        max_p = float(prices.max())
        if min_p <= 0:
            continue
        spreads.append(
            VendorSpread(
                vendor=str(vendor),
                n_agencies=int(len(group)),
                n_awards=int(group["n_awards"].sum()),
                min_avg_price=min_p,
                max_avg_price=max_p,
                spread_ratio=max_p / min_p,
                total_spend_usd=float(group["total_spend"].sum()),
            )
        )

    spreads.sort(key=lambda s: s.spread_ratio, reverse=True)
    return tuple(spreads)


# ---------------------------------------------------------------------------
# Step 4 — agency-level savings opportunity
# ---------------------------------------------------------------------------


def _agency_opportunities(df: pd.DataFrame) -> tuple[AgencyOpportunity, ...]:
    """Per-agency savings if it bought every shared vendor at the best peer price.

    For every (vendor, agency) cell, find the lowest-priced peer agency
    that buys from the same vendor. Multiply the agency's award count by
    the price delta (current avg - best peer avg). Sum across vendors to
    get the agency's total savings opportunity.
    """
    if df.empty:
        return ()

    # (vendor, agency) -> mean price + count + spend
    cell = (
        df.groupby(["vendor", "agency"])
        .agg(
            avg_price=("award_amount", "mean"),
            n_awards=("award_amount", "size"),
            spend=("award_amount", "sum"),
        )
        .reset_index()
    )

    # For each vendor: which agency has the lowest avg price?
    best_per_vendor = (
        cell.sort_values(["vendor", "avg_price"])
        .groupby("vendor", as_index=False)
        .first()
        .rename(columns={"agency": "best_peer_agency", "avg_price": "best_peer_price"})[
            ["vendor", "best_peer_agency", "best_peer_price"]
        ]
    )

    joined = cell.merge(best_per_vendor, on="vendor", how="left")
    # Per-cell savings: (current - best) * n_awards, floored at 0
    joined["price_delta"] = (joined["avg_price"] - joined["best_peer_price"]).clip(lower=0)
    joined["savings_usd"] = joined["price_delta"] * joined["n_awards"]
    # Don't credit an agency for "savings" against itself
    joined.loc[joined["agency"] == joined["best_peer_agency"], "savings_usd"] = 0.0

    # Vendor-level cohort thickness: how many distinct buyers does this
    # vendor serve? Vendors with only one buyer can't be benchmarked.
    n_agencies_per_vendor = (
        joined.groupby("vendor")["agency"].nunique().reset_index(name="n_agencies")
    )

    # Aggregate to agency level. The "best_peer_agency" shown is the one
    # that contributes the largest single-vendor savings line for the agency
    # — that's the most actionable lead.
    opportunities: list[AgencyOpportunity] = []
    for agency, agroup in joined.groupby("agency"):
        # Restrict to vendors that exist for >=2 agencies (true cohort)
        cohort = agroup.merge(n_agencies_per_vendor, on="vendor", how="left")
        cohort = cohort[cohort["n_agencies"] >= _MIN_AGENCIES_FOR_BENCHMARK]
        if cohort.empty:
            continue

        total_savings = float(cohort["savings_usd"].sum())
        n_awards = int(cohort["n_awards"].sum())
        spend = float(cohort["spend"].sum())
        current_avg = float(spend / n_awards) if n_awards else 0.0

        # Use the cohort's spend-weighted best peer price as the reference
        weighted_best = (
            float((cohort["best_peer_price"] * cohort["n_awards"]).sum() / n_awards)
            if n_awards
            else 0.0
        )
        # The best peer for the agency = whichever single vendor row contributed
        # the largest savings (the partner's actionable lead).
        if cohort["savings_usd"].max() > 0:
            top_row = cohort.loc[cohort["savings_usd"].idxmax()]
            best_peer = str(top_row["best_peer_agency"])
        else:
            best_peer = "—"

        opportunities.append(
            AgencyOpportunity(
                agency=str(agency),
                current_avg_price_per_award=current_avg,
                best_peer_price_per_award=weighted_best,
                n_awards_in_cohort=n_awards,
                spend_in_cohort_usd=spend,
                savings_if_matched_best_usd=total_savings,
                best_peer_agency=best_peer,
            )
        )

    opportunities.sort(key=lambda o: o.savings_if_matched_best_usd, reverse=True)
    return tuple(opportunities)


# ---------------------------------------------------------------------------
# Step 5 — formatting helpers (mirror explain.py)
# ---------------------------------------------------------------------------


def _scaled(usd: float) -> str:
    """Render dollar amount at the right precision."""
    if abs(usd) >= 1e9:
        return f"${usd / 1e9:,.2f}B"
    if abs(usd) >= 1e6:
        return f"${usd / 1e6:,.1f}M"
    if abs(usd) >= 1e3:
        return f"${usd / 1e3:,.0f}K"
    return f"${usd:,.0f}"


def _to_roman(n: int) -> str:
    """Tiny roman numeral helper — matches the explain.py / normalize.py vibe."""
    table = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ]
    out, x = "", n
    for value, symbol in table:
        while x >= value:
            out += symbol
            x -= value
    return out or "I"


# ---------------------------------------------------------------------------
# Step 6 — HTML render (editorial-letterpress, twin to explain.py)
# ---------------------------------------------------------------------------


_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Vendor benchmarking — {psc_label}</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&family=EB+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&family=Newsreader:ital,wght@0,300;1,300&family=JetBrains+Mono:wght@400&display=swap" rel="stylesheet" />
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
    --accent-2: #93331f;
    --gold:     #8a6f1a;
    --max:      980px;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  html {{ background: #ece4cb; }}
  body {{
    background:
      radial-gradient(ellipse 1200px 800px at 50% -100px, rgba(255,248,220,0.6), transparent 70%),
      radial-gradient(ellipse 600px 400px at 80% 120%, rgba(107,20,20,0.04), transparent 70%),
      var(--paper);
    color: var(--ink);
    font-family: 'EB Garamond', 'Iowan Old Style', Georgia, serif;
    font-size: 17px;
    line-height: 1.6;
    font-feature-settings: "liga", "dlig", "onum", "kern";
    text-rendering: optimizeLegibility;
    -webkit-font-smoothing: antialiased;
    padding: 64px 20px 96px;
  }}
  body::before {{
    content: ''; position: fixed; inset: 0;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0.42  0 0 0 0 0.34  0 0 0 0 0.18  0 0 0 0.07 0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>");
    opacity: 0.32; pointer-events: none; z-index: 0; mix-blend-mode: multiply;
  }}
  .sheet {{
    max-width: var(--max); margin: 0 auto; background: var(--page);
    position: relative; z-index: 1; padding: 80px 76px 72px;
    box-shadow:
      0 1px 0 var(--rule-soft),
      0 30px 60px -30px rgba(60, 40, 15, 0.18),
      0 8px 18px -6px rgba(60, 40, 15, 0.08);
    border: 1px solid rgba(194, 173, 132, 0.45);
  }}
  .letterhead {{
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: 24px; margin-bottom: 48px;
  }}
  .wordmark {{
    font-family: 'Cormorant Garamond', serif; font-weight: 500; font-style: italic;
    font-size: 21px; letter-spacing: 0.01em;
    display: flex; align-items: center; gap: 14px; color: var(--ink);
  }}
  .wordmark .seal {{
    width: 36px; height: 36px; border-radius: 50%;
    border: 1px solid var(--accent); color: var(--accent);
    display: inline-flex; align-items: center; justify-content: center;
    font-family: 'Cormorant Garamond', serif; font-style: italic;
    font-size: 18px; font-weight: 500;
    background: rgba(107, 20, 20, 0.04);
  }}
  .letterhead-meta {{
    text-align: right; font-family: 'Newsreader', 'EB Garamond', serif;
    font-style: italic; font-weight: 300; font-size: 13px; line-height: 1.55;
    color: var(--ink-faint); letter-spacing: 0.02em;
  }}
  .letterhead-meta strong {{
    display: block; color: var(--ink-dim); font-style: normal; font-weight: 500;
    font-variant: small-caps; letter-spacing: 0.14em; font-size: 11px;
    margin-bottom: 2px;
  }}

  .eyebrow {{
    font-variant: small-caps; letter-spacing: 0.18em; font-size: 12px;
    color: var(--accent); font-weight: 600; margin-bottom: 14px;
    display: inline-block;
  }}
  .eyebrow::before {{ content: '— '; color: var(--rule); }}
  .eyebrow::after  {{ content: ' —'; color: var(--rule); }}
  h1 {{
    font-family: 'Cormorant Garamond', serif; font-weight: 400;
    font-size: 46px; line-height: 1.06; letter-spacing: -0.005em;
    margin: 0 0 18px; color: var(--ink);
  }}
  h1 em {{ font-style: italic; color: var(--accent); font-weight: 500; }}
  .lede {{
    font-family: 'EB Garamond', serif; font-style: italic; color: var(--ink-dim);
    font-size: 19px; line-height: 1.55; margin: 0; max-width: 60ch;
  }}

  .ornament {{
    text-align: center; color: var(--rule);
    font-family: 'Cormorant Garamond', serif;
    font-size: 22px; letter-spacing: 1.2em;
    margin: 40px 0; padding-left: 1.2em;
  }}
  .ornament::before {{ content: '✦  ✦  ✦'; }}

  .stats-strip {{
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 0; margin: 28px 0 48px;
    border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule);
    padding: 18px 0;
  }}
  .stat {{ text-align: center; padding: 0 16px; border-right: 1px solid var(--rule-soft); }}
  .stat:last-child {{ border-right: none; }}
  .stat-label {{
    font-variant: small-caps; letter-spacing: 0.16em; font-size: 11px;
    color: var(--ink-faint); font-weight: 500; margin-bottom: 6px;
  }}
  .stat-num {{
    font-family: 'Cormorant Garamond', serif; font-weight: 500;
    font-size: 26px; line-height: 1; color: var(--ink);
    font-feature-settings: "lnum";
  }}
  .stat-sub {{
    font-family: 'Newsreader', serif; font-style: italic; font-weight: 300;
    font-size: 11px; color: var(--ink-faint); margin-top: 4px;
  }}

  h2 {{
    font-family: 'Cormorant Garamond', serif; font-weight: 500;
    font-size: 30px; margin: 48px 0 12px; color: var(--ink);
  }}
  h2 .pretitle {{
    display: block;
    font-variant: small-caps; letter-spacing: 0.18em;
    font-size: 11px; color: var(--accent); font-weight: 600;
    margin-bottom: 6px;
  }}

  table.ledger {{
    width: 100%; border-collapse: collapse;
    font-family: 'EB Garamond', serif; font-size: 14px;
    margin: 12px 0 32px; font-feature-settings: "lnum", "tnum";
  }}
  table.ledger th, table.ledger td {{
    text-align: left; padding: 8px 10px;
    border-bottom: 1px dotted var(--rule-soft);
  }}
  table.ledger th {{
    font-variant: small-caps; letter-spacing: 0.14em; font-size: 11px;
    font-weight: 600; color: var(--ink-faint);
    border-bottom: 1px solid var(--rule);
  }}
  table.ledger td.num {{ text-align: right; font-feature-settings: "lnum","tnum"; }}
  table.ledger td.savings {{ color: var(--accent); font-weight: 600; }}
  table.ledger td code {{
    font-family: 'JetBrains Mono', monospace; font-size: 12px;
    color: var(--ink-dim); background: rgba(194, 173, 132, 0.18);
    padding: 1px 5px; border-radius: 2px;
  }}
  table.ledger tr.rank-1 td.num.savings {{ font-size: 16px; }}

  .recommendation {{
    border-left: 3px solid var(--accent);
    background: rgba(255, 247, 215, 0.55);
    padding: 14px 18px; margin: 12px 0;
  }}
  .recommendation .r-head {{
    font-variant: small-caps; letter-spacing: 0.14em; font-size: 11px;
    color: var(--accent); font-weight: 600; margin-bottom: 4px;
  }}
  .recommendation p {{ margin: 0; }}
  .recommendation p strong {{ color: var(--ink); }}

  .colophon {{
    margin-top: 64px; padding-top: 22px;
    border-top: 1px solid var(--rule);
    font-family: 'Newsreader', 'EB Garamond', serif;
    font-style: italic; font-weight: 300;
    font-size: 12px; line-height: 1.6; color: var(--ink-faint);
    text-align: center;
  }}
  .colophon code {{
    font-family: 'JetBrains Mono', monospace; font-style: normal;
    font-size: 11px; color: var(--ink-dim);
    background: rgba(194, 173, 132, 0.18);
    padding: 1px 6px; border-radius: 2px;
  }}
  .colophon .signature {{
    font-family: 'Cormorant Garamond', serif; font-style: italic;
    font-size: 16px; color: var(--ink-dim); margin-bottom: 12px;
  }}
  .colophon .signature::before {{
    content: ''; display: block; width: 120px; height: 1px;
    background: var(--ink-faint); opacity: 0.5; margin: 0 auto 10px;
  }}

  @media (max-width: 720px) {{
    body {{ padding: 32px 8px 64px; font-size: 16px; }}
    .sheet {{ padding: 48px 24px 56px; }}
    h1 {{ font-size: 32px; }}
    .stats-strip {{ grid-template-columns: repeat(2, 1fr); gap: 12px; padding: 14px 0; }}
    .stat {{ border-right: none; border-bottom: 1px dotted var(--rule-soft); padding-bottom: 12px; }}
    .letterhead {{ flex-direction: column; gap: 8px; }}
    .letterhead-meta {{ text-align: left; }}
  }}
  @media print {{
    html, body {{ background: white; }}
    body::before {{ display: none; }}
    .sheet {{ box-shadow: none; border: none; padding: 24mm; max-width: none; }}
  }}
</style>
</head>
<body>
<article class="sheet">

  <header class="letterhead">
    <div class="wordmark">
      <span class="seal">&para;</span>
      <span>Private Equity &times; AI</span>
    </div>
    <div class="letterhead-meta">
      <strong>Vendor benchmarking</strong>
      {psc_code} &middot; FY{fiscal_year}<br />
      {as_of}
    </div>
  </header>

  <div class="eyebrow">Cross-portco price variance for {psc_label}</div>
  <h1>{n_agencies} buyers, <em>{total_savings_scaled} on the table.</em></h1>
  <p class="lede">{lede}</p>

  <div class="stats-strip">
    <div class="stat">
      <div class="stat-label">Contracts</div>
      <div class="stat-num">{n_contracts:,}</div>
      <div class="stat-sub">awards in cohort</div>
    </div>
    <div class="stat">
      <div class="stat-label">Buyers</div>
      <div class="stat-num">{n_agencies}</div>
      <div class="stat-sub">awarding agencies</div>
    </div>
    <div class="stat">
      <div class="stat-label">Vendors</div>
      <div class="stat-num">{n_vendors}</div>
      <div class="stat-sub">named recipients</div>
    </div>
    <div class="stat">
      <div class="stat-label">Savings</div>
      <div class="stat-num">{total_savings_scaled}</div>
      <div class="stat-sub">if all match best</div>
    </div>
  </div>

  <div class="ornament"></div>

  <h2><span class="pretitle">I</span>Top savings opportunities by buyer</h2>
  <p style="margin:0 0 14px;color:var(--ink-dim);font-style:italic;">
    Each row asks: if this buyer paid the same per-award price its lowest
    peer pays for the same vendor, how much would it have saved on the
    awards it actually placed? The methodology is Apollo's: the same SKU,
    bought by N portcos, at N different prices &mdash; surface the gap.
  </p>
  <table class="ledger">
    <thead>
      <tr>
        <th>#</th>
        <th>Awarding agency</th>
        <th class="num">Awards</th>
        <th class="num">Spend</th>
        <th class="num">Avg paid</th>
        <th class="num">Best peer avg</th>
        <th class="num">Savings opp.</th>
        <th>Best peer</th>
      </tr>
    </thead>
    <tbody>
      {opportunity_rows}
    </tbody>
  </table>

  <h2><span class="pretitle">II</span>Vendors with the widest cross-buyer spread</h2>
  <p style="margin:0 0 14px;color:var(--ink-dim);font-style:italic;">
    The vendors below charge the same service to different agencies at
    materially different price points. Each row is a renegotiation lead.
  </p>
  <table class="ledger">
    <thead>
      <tr>
        <th>#</th>
        <th>Vendor</th>
        <th class="num">Buyers</th>
        <th class="num">Awards</th>
        <th class="num">Min avg</th>
        <th class="num">Max avg</th>
        <th class="num">Spread</th>
        <th class="num">Total spend</th>
      </tr>
    </thead>
    <tbody>
      {vendor_rows}
    </tbody>
  </table>

  <h2><span class="pretitle">III</span>Recommendations</h2>
  {recommendation_blocks}

  <footer class="colophon">
    <div class="signature">Composed at the procurement-benchmark layer.</div>
    Generated by <code>benchmark_vendors</code>. Source: USAspending.gov
    public awards data, PSC <code>{psc_code}</code>, FY{fiscal_year}.
    Every figure traces to a contract record &mdash; see
    <code>{json_basename}</code> for the full vendor &times; agency price matrix.<br />
    {as_of}.
  </footer>

</article>
</body>
</html>
"""


def _render_opportunity_rows(opps: tuple[AgencyOpportunity, ...]) -> str:
    if not opps:
        return (
            '<tr><td colspan="8" style="color:var(--ink-faint);font-style:italic;">'
            "No agency-level opportunities surfaced — vendor cohort too thin."
            "</td></tr>"
        )

    rows: list[str] = []
    for idx, opp in enumerate(opps[:_TOP_OPPORTUNITIES_IN_HTML], start=1):
        rows.append(
            f"<tr class=\"rank-{idx}\">"
            f"<td>{idx}</td>"
            f"<td>{opp.agency}</td>"
            f"<td class=\"num\">{opp.n_awards_in_cohort:,}</td>"
            f"<td class=\"num\">{_scaled(opp.spend_in_cohort_usd)}</td>"
            f"<td class=\"num\">{_scaled(opp.current_avg_price_per_award)}</td>"
            f"<td class=\"num\">{_scaled(opp.best_peer_price_per_award)}</td>"
            f"<td class=\"num savings\">{_scaled(opp.savings_if_matched_best_usd)}</td>"
            f"<td>{opp.best_peer_agency}</td>"
            f"</tr>"
        )
    return "\n      ".join(rows)


def _render_vendor_rows(spreads: tuple[VendorSpread, ...]) -> str:
    if not spreads:
        return (
            '<tr><td colspan="8" style="color:var(--ink-faint);font-style:italic;">'
            "No vendor served &gt;= 2 agencies in the cohort."
            "</td></tr>"
        )

    rows: list[str] = []
    for idx, sp in enumerate(spreads[:_TOP_OPPORTUNITIES_IN_HTML], start=1):
        rows.append(
            f"<tr>"
            f"<td>{idx}</td>"
            f"<td>{sp.vendor}</td>"
            f"<td class=\"num\">{sp.n_agencies}</td>"
            f"<td class=\"num\">{sp.n_awards:,}</td>"
            f"<td class=\"num\">{_scaled(sp.min_avg_price)}</td>"
            f"<td class=\"num\">{_scaled(sp.max_avg_price)}</td>"
            f"<td class=\"num savings\">{sp.spread_ratio:,.1f}&times;</td>"
            f"<td class=\"num\">{_scaled(sp.total_spend_usd)}</td>"
            f"</tr>"
        )
    return "\n      ".join(rows)


def _render_recommendation_blocks(
    opps: tuple[AgencyOpportunity, ...], psc_label: str
) -> str:
    if not opps:
        return (
            '<p style="color:var(--ink-faint);font-style:italic;">'
            "No actionable recommendations — vendor cohort too thin to "
            "benchmark."
            "</p>"
        )

    blocks: list[str] = []
    for idx, opp in enumerate(opps[:5], start=1):
        roman = _to_roman(idx)
        blocks.append(
            f"""
  <div class="recommendation">
    <div class="r-head">{roman} &middot; {opp.agency}</div>
    <p>
      Across <strong>{opp.n_awards_in_cohort:,}</strong> awards in the
      {psc_label} cohort, {opp.agency} paid an average of
      <strong>{_scaled(opp.current_avg_price_per_award)}</strong> per award
      versus a spend-weighted best peer price of
      <strong>{_scaled(opp.best_peer_price_per_award)}</strong>. Closing
      that gap on the actual award volume would recover
      <strong>{_scaled(opp.savings_if_matched_best_usd)}</strong>.
      Highest single lead: shadow {opp.best_peer_agency}'s contract terms
      with the vendor responsible for the largest share of the gap, then
      run a short RFP to the same vendor pool with that benchmark in hand.
    </p>
  </div>
"""
        )
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# Step 7 — orchestrator (the public tool)
# ---------------------------------------------------------------------------


def _resolve_output_dir() -> Path:
    """Match the repo convention: write to `finance_output/` at repo root."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "finance_output"
        if candidate.exists() and candidate.is_dir():
            return candidate
    cwd_candidate = Path.cwd() / "finance_output"
    cwd_candidate.mkdir(parents=True, exist_ok=True)
    return cwd_candidate


def _validate_inputs(
    psc_code: str, fiscal_year: int, max_records: int, cache_dir: str
) -> None:
    """Fail-fast validation on the input boundary."""
    if not isinstance(psc_code, str) or not psc_code.strip():
        raise ToolError("psc_code must be a non-empty string (e.g. 'D310').")
    if not isinstance(fiscal_year, int) or not (2008 <= fiscal_year <= 2099):
        raise ToolError(
            f"fiscal_year must be an int between 2008 and 2099 (got {fiscal_year!r})."
        )
    if not isinstance(max_records, int) or max_records <= 0:
        raise ToolError(
            f"max_records must be a positive int (got {max_records!r})."
        )
    if max_records > _MAX_RECORDS_HARD_CAP:
        raise ToolError(
            f"max_records must be <= {_MAX_RECORDS_HARD_CAP:,} (got {max_records:,}). "
            f"USAspending pages at 100/req; large fetches should be batched."
        )
    if not isinstance(cache_dir, str) or not cache_dir.strip():
        raise ToolError("cache_dir must be a non-empty string path.")


def benchmark_vendors(
    psc_code: str = "D310",
    fiscal_year: int = 2024,
    max_records: int = 2000,
    cache_dir: str = "/tmp/usaspending_cache",
    output_filename: str | None = None,
) -> dict:
    """
    Apollo-style cross-portco vendor benchmarking against public federal data.

    Treats every awarding agency as a "portco" and every recipient as a
    vendor. For one PSC code (the federal service-line "SKU") and one
    fiscal year, surfaces the per-award price gap between the best buyer
    and every peer, ranked by total dollar opportunity.

    Args:
        psc_code: Federal Product / Service Code to benchmark
            (default 'D310' — IT Support Services).
        fiscal_year: US federal fiscal year (Oct 1 of FY-1 → Sep 30 of FY).
        max_records: Cap on contracts fetched. Hard cap 50,000.
        cache_dir: Directory for the API response cache.
        output_filename: Optional basename stem for HTML/JSON outputs.
            Defaults to ``benchmark_<psc>_<fy>``.

    Returns:
        dict with:
          - report_path:                  absolute path to the HTML memo
          - json_path:                    absolute path to the JSON sidecar
          - n_contracts:                  contracts in the analysis cohort
          - n_agencies:                   distinct awarding agencies (buyers)
          - n_vendors:                    distinct recipients (vendors)
          - total_savings_opportunity_usd: sum of per-agency savings if
                                            every agency moved to the best
                                            peer price for shared vendors
          - top_savings_per_agency:       list[dict] of AgencyOpportunity
                                            entries (sorted desc), capped
                                            to the top 25 for the return

    Raises:
        ToolError: on validation failure or thin-cohort outcomes.
    """
    _validate_inputs(psc_code, fiscal_year, max_records, cache_dir)

    psc_code_clean = psc_code.strip().upper()
    psc_label = _PSC_LABELS.get(psc_code_clean, f"PSC {psc_code_clean}")

    # Step 1: fetch (cache-first)
    try:
        records, fetch_meta = fetch_awards(
            psc_code=psc_code_clean,
            fiscal_year=fiscal_year,
            max_records=max_records,
            cache_dir=cache_dir,
        )
    except RuntimeError as exc:
        raise ToolError(str(exc)) from exc

    if not records:
        raise ToolError(
            f"USAspending returned zero contracts for PSC {psc_code_clean} "
            f"FY{fiscal_year}. Try a different PSC code or fiscal year."
        )

    # Step 2: tidy frame + matrix
    df = _records_to_frame(records)
    if df.empty:
        raise ToolError(
            f"All {len(records)} fetched records dropped during cleaning "
            f"(missing agency / vendor / non-positive amount). Cohort empty."
        )

    n_contracts = int(len(df))
    n_agencies = int(df["agency"].nunique())
    n_vendors = int(df["vendor"].nunique())

    if n_agencies < _MIN_AGENCIES_FOR_BENCHMARK:
        raise ToolError(
            f"Only {n_agencies} awarding agency in the cohort — need >=2 "
            f"to benchmark cross-buyer prices."
        )
    if n_vendors < _MIN_VENDORS_FOR_BENCHMARK:
        raise ToolError(
            f"Only {n_vendors} vendor in the cohort — need >=2 to benchmark."
        )

    matrix = _build_price_matrix(df)
    spreads = _vendor_spreads(df)
    opportunities = _agency_opportunities(df)
    total_savings = float(sum(o.savings_if_matched_best_usd for o in opportunities))

    # Step 3: write outputs
    out_dir = _resolve_output_dir()
    stem = output_filename or f"benchmark_{psc_code_clean}_FY{fiscal_year}"
    if stem.endswith(".html"):
        stem = stem[:-5]

    report_path = out_dir / f"{stem}.html"
    json_path = out_dir / f"{stem}.json"

    # Build the JSON sidecar — full price matrix + per-agency recommendations
    matrix_json: dict[str, dict[str, float]] = {}
    if not matrix.empty:
        for vendor, row in matrix.iterrows():
            matrix_json[str(vendor)] = {
                str(agency): float(price)
                for agency, price in row.items()
                if pd.notna(price)
            }

    sidecar = {
        "as_of": date.today().isoformat(),
        "source": "USAspending.gov public awards data",
        "psc_code": psc_code_clean,
        "psc_label": psc_label,
        "fiscal_year": fiscal_year,
        "fetched_records": fetch_meta.n_records,
        "from_cache": fetch_meta.from_cache,
        "cache_path": str(fetch_meta.cache_path),
        "n_contracts_in_cohort": n_contracts,
        "n_agencies": n_agencies,
        "n_vendors": n_vendors,
        "total_savings_opportunity_usd": total_savings,
        "agency_opportunities": [asdict(o) for o in opportunities],
        "vendor_spreads": [asdict(s) for s in spreads],
        "vendor_agency_price_matrix": matrix_json,
    }
    json_path.write_text(json.dumps(sidecar, indent=2, default=str), encoding="utf-8")

    # Build the HTML
    lede = (
        f"A benchmarking pass over {n_contracts:,} federal {psc_label} "
        f"contracts in fiscal year {fiscal_year} surfaces "
        f"{_scaled(total_savings)} of cross-buyer price gap. Each "
        f"awarding agency is treated as a portco; each recipient as a "
        f"vendor. The same vendor sells the same service to multiple "
        f"agencies at materially different per-award prices &mdash; the "
        f"gap is the renegotiation opportunity. The methodology mirrors "
        f"what Apollo's 50-person procurement team does with their own "
        f"portfolio: line up the same SKU, surface the spread, move every "
        f"buyer to the best price."
    )

    html = _HTML.format(
        psc_code=psc_code_clean,
        psc_label=psc_label,
        fiscal_year=fiscal_year,
        as_of=date.today().isoformat(),
        n_contracts=n_contracts,
        n_agencies=n_agencies,
        n_vendors=n_vendors,
        total_savings_scaled=_scaled(total_savings),
        lede=lede,
        opportunity_rows=_render_opportunity_rows(opportunities),
        vendor_rows=_render_vendor_rows(spreads),
        recommendation_blocks=_render_recommendation_blocks(opportunities, psc_label),
        json_basename=json_path.name,
    )
    report_path.write_text(html, encoding="utf-8")

    return {
        "report_path": str(report_path),
        "json_path": str(json_path),
        "n_contracts": n_contracts,
        "n_agencies": n_agencies,
        "n_vendors": n_vendors,
        "total_savings_opportunity_usd": total_savings,
        "top_savings_per_agency": [asdict(o) for o in opportunities[:25]],
    }
