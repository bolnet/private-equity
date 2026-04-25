"""
bx_report — Render a cross-portco benchmark as a self-contained static HTML.

Reuses DX's design system (dark palette + DM Sans) and chart-embed pattern
(base64 PNGs inline, no external image deps). One file, emailable.

Sections:
  • Corpus header (portco count, total identified $, coverage)
  • Fund-level archetype index (p10/median/p90 bars per archetype)
  • Per-portco rank table (top metric)
  • Peer groups per portco (who looks like whom)
  • Optional: one highlighted portco's deep-drill panel
"""
from __future__ import annotations

import base64
import html
import io
import json
import os
from datetime import datetime, timezone
from typing import Any

from fastmcp.exceptions import ToolError

from finance_mcp.bx.archetype_index import bx_archetype_index
from finance_mcp.bx.peer_group import bx_peer_group
from finance_mcp.bx.rank import bx_portco_rank
from finance_mcp.bx.session import get_session
from finance_mcp.output import ensure_output_dirs, SCRIPT_DIR


_CSS = """
:root {
  --bg-primary:#0a0a0b; --bg-secondary:#111113; --bg-card:#16161a;
  --bg-card-hover:#1c1c21; --border:#2a2a30; --border-subtle:#1e1e24;
  --text-primary:#f0f0f2; --text-secondary:#94949e; --text-muted:#5c5c66;
  --accent:#00d4aa; --red:#ff4d6a; --amber:#ffb347; --blue:#4d9cff;
}
*{margin:0;padding:0;box-sizing:border-box}
body{
  font-family:'DM Sans',-apple-system,BlinkMacSystemFont,sans-serif;
  background:var(--bg-primary); color:var(--text-primary);
  line-height:1.55; padding:48px 24px 96px;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1200px; margin:0 auto}
.eyebrow{color:var(--accent); font-size:12px; font-weight:500;
  letter-spacing:.12em; text-transform:uppercase; margin-bottom:12px}
h1{font-size:34px; font-weight:500; letter-spacing:-.02em; line-height:1.2}
h1 em{font-style:normal; color:var(--accent)}
.sub{color:var(--text-secondary); margin-top:12px; font-size:16px}
.meta{display:flex; flex-wrap:wrap; gap:24px; margin-top:24px;
  padding:16px 20px; background:var(--bg-card);
  border:1px solid var(--border-subtle); border-radius:12px}
.meta-item{display:flex; flex-direction:column; gap:4px}
.meta-label{font-size:11px; color:var(--text-muted);
  letter-spacing:.08em; text-transform:uppercase}
.meta-value{font-size:18px; font-weight:500}
.meta-value.good{color:var(--accent)}

.section{margin-top:44px}
.section h2{font-size:22px; font-weight:500; margin-bottom:12px;
  letter-spacing:-.01em}
.section .sub-h{color:var(--text-muted); font-size:13px; margin-bottom:16px}

/* Archetype index */
.arche-grid{display:grid; gap:10px}
.arche-row{display:grid; grid-template-columns:140px 1fr 100px;
  gap:16px; align-items:center; padding:12px 14px;
  background:var(--bg-card); border:1px solid var(--border-subtle);
  border-radius:10px}
.arche-name{font-weight:500; font-size:14px;
  color:var(--text-primary); text-transform:capitalize}
.arche-range{position:relative; height:24px;
  background:var(--border-subtle); border-radius:4px}
.arche-range .band{position:absolute; top:0; height:100%;
  background:var(--accent); opacity:.28; border-radius:4px}
.arche-range .median{position:absolute; top:-3px; bottom:-3px;
  width:2px; background:var(--accent)}
.arche-range .label{position:absolute; top:6px; right:10px;
  font-size:11px; color:var(--text-muted)}
.arche-total{font-variant-numeric:tabular-nums; font-size:14px;
  font-weight:500; text-align:right}

/* Rank table */
.rank-tbl{width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums}
.rank-tbl th{text-align:left; font-weight:500; font-size:11px;
  color:var(--text-muted); text-transform:uppercase;
  letter-spacing:.08em; padding:10px 12px;
  border-bottom:1px solid var(--border-subtle)}
.rank-tbl td{padding:14px 12px; border-bottom:1px solid var(--border-subtle);
  font-size:14px}
.rank-tbl tr:last-child td{border-bottom:none}
.rank-tbl tr:hover td{background:var(--bg-card-hover)}
.rank-cell{color:var(--accent); font-weight:500}
.pct-cell{color:var(--text-secondary); font-size:13px}
.neg{color:var(--red)}

/* Peer groups */
.peer-card{padding:16px 20px; background:var(--bg-card);
  border:1px solid var(--border-subtle); border-radius:12px; margin-bottom:12px}
.peer-head{display:flex; justify-content:space-between; align-items:baseline;
  margin-bottom:10px}
.peer-portco{font-weight:500; font-size:15px}
.peer-tag{font-size:11px; color:var(--blue); letter-spacing:.04em;
  text-transform:uppercase}
.peer-row{display:grid; grid-template-columns:1fr auto auto;
  gap:14px; padding:6px 0; font-size:13px;
  color:var(--text-secondary)}
.peer-row .name{color:var(--text-primary)}
.peer-row .score{font-variant-numeric:tabular-nums}

.footer{margin-top:64px; padding-top:24px;
  border-top:1px solid var(--border-subtle); color:var(--text-muted);
  font-size:12px; display:flex; justify-content:space-between}
.disclaimer{margin-top:32px; padding:12px 16px; background:var(--bg-card);
  border:1px solid var(--border-subtle); border-radius:8px;
  color:var(--text-muted); font-size:12px; font-style:italic}
"""


def _fmt_usd(value: float) -> str:
    if value is None:
        return "$—"
    absval = abs(value)
    sign = "−" if value < 0 else ""
    if absval >= 1_000_000_000:
        return f"{sign}${absval / 1_000_000_000:.2f}B"
    if absval >= 1_000_000:
        return f"{sign}${absval / 1_000_000:.2f}M"
    if absval >= 1_000:
        return f"{sign}${absval / 1_000:.1f}k"
    return f"{sign}${absval:,.0f}"


def _render_archetype_section(index: dict) -> str:
    stats = index["archetype_stats"]
    # Scale: use the largest p90 across all archetypes to set the band width
    max_p90 = max((s["p90_impact_usd"] for s in stats), default=1.0) or 1.0

    rows = []
    for s in stats:
        name = s["archetype"]
        count = s["portco_count_with_archetype"]
        total = s["total_impact_usd"]
        p10 = s["p10_impact_usd"]
        median = s["median_impact_usd"]
        p90 = s["p90_impact_usd"]
        if count == 0:
            rows.append(
                f"""
                <div class="arche-row">
                  <div class="arche-name">{html.escape(name)}</div>
                  <div class="arche-range"><div class="label">no opportunities</div></div>
                  <div class="arche-total" style="color:var(--text-muted)">—</div>
                </div>
                """
            )
            continue
        left_pct = p10 / max_p90 * 100.0
        right_pct = p90 / max_p90 * 100.0
        med_pct = median / max_p90 * 100.0
        band_w = max(right_pct - left_pct, 1.0)
        rows.append(
            f"""
            <div class="arche-row">
              <div class="arche-name">{html.escape(name)} <span style="font-weight:400;color:var(--text-muted);font-size:12px">({count} portco{"s" if count != 1 else ""})</span></div>
              <div class="arche-range">
                <div class="band" style="left:{left_pct:.1f}%; width:{band_w:.1f}%"></div>
                <div class="median" style="left:{med_pct:.1f}%"></div>
                <div class="label">p10 {_fmt_usd(p10)} · med {_fmt_usd(median)} · p90 {_fmt_usd(p90)}</div>
              </div>
              <div class="arche-total">{_fmt_usd(total)}</div>
            </div>
            """
        )
    return (
        '<div class="section">'
        "<h2>Fund-wide archetype distribution</h2>"
        '<div class="sub-h">P10 / median / P90 per archetype across all portcos. '
        "Rightmost column is cumulative $ per archetype.</div>"
        f'<div class="arche-grid">{"".join(rows)}</div>'
        "</div>"
    )


def _render_rank_table(corpus_id: str, metric: str) -> str:
    session = get_session(corpus_id)
    rows_html = []
    portcos_sorted = session.portco_profiles_df.sort_values(
        metric, ascending=False
    )
    for _, row in portcos_sorted.iterrows():
        rank_result = bx_portco_rank(corpus_id, row["portco_id"], metric)
        impact = float(row["total_projected_impact_usd_annual"])
        impact_cls = "neg" if impact < 0 else ""
        rows_html.append(
            f"""
            <tr>
              <td class="rank-cell">#{rank_result['rank']}</td>
              <td>{html.escape(str(row['portco_id']))}</td>
              <td style="color:var(--text-secondary)">{html.escape(str(row['vertical']))}</td>
              <td class="{impact_cls}">{_fmt_usd(impact)}/yr</td>
              <td>{int(row['opportunity_count'])}</td>
              <td class="pct-cell">{rank_result['percentile']:.0f}th pctile</td>
            </tr>
            """
        )
    return (
        '<div class="section">'
        f"<h2>Ranked by {metric.replace('_', ' ')}</h2>"
        '<table class="rank-tbl"><thead>'
        "<tr><th>Rank</th><th>Portco</th><th>Vertical</th><th>Identified</th>"
        "<th>Opps</th><th>Fund Percentile</th></tr>"
        f"</thead><tbody>{''.join(rows_html)}</tbody></table>"
        "</div>"
    )


def _render_peer_cards(corpus_id: str) -> str:
    session = get_session(corpus_id)
    if session.portco_count < 2:
        return ""
    cards = []
    for _, row in session.portco_profiles_df.iterrows():
        peers = bx_peer_group(corpus_id, str(row["portco_id"]), top_n=3)
        peer_rows = []
        for p in peers["peers"]:
            peer_rows.append(
                f"""
                <div class="peer-row">
                  <div class="name">{html.escape(p['portco_id'])}</div>
                  <div>{html.escape(p['top_archetype'])}</div>
                  <div class="score">sim {p['similarity_score']:.2f}</div>
                </div>
                """
            )
        cards.append(
            f"""
            <div class="peer-card">
              <div class="peer-head">
                <div class="peer-portco">{html.escape(row['portco_id'])}</div>
                <div class="peer-tag">top archetype · {html.escape(peers['reference_top_archetype'])}</div>
              </div>
              {''.join(peer_rows) or '<div class="peer-row"><div>(no peers)</div><div></div><div></div></div>'}
            </div>
            """
        )
    return (
        '<div class="section">'
        "<h2>Peer groups</h2>"
        '<div class="sub-h">Top-3 most similar portcos by archetype-profile shape '
        "(cosine similarity on per-archetype impact + persistence).</div>"
        f"{''.join(cards)}"
        "</div>"
    )


def bx_report(
    corpus_id: str,
    rank_by: str = "total_projected_impact_usd_annual",
    output_filename: str = "",
) -> dict:
    """
    Render a cross-portco benchmark as self-contained static HTML.

    Args:
        corpus_id:       From a prior bx_ingest_corpus call.
        rank_by:         Metric to sort the rank table by.
        output_filename: Optional filename (basename). Defaults to
                         'bx_report_<corpus_id>.html'.

    Returns:
        dict with path, json_path, bytes_written, portco_count.
    """
    session = get_session(corpus_id)
    if session.portco_count == 0:
        raise ToolError("Cannot render a report for an empty corpus.")

    index = bx_archetype_index(corpus_id)
    arche_html = _render_archetype_section(index)
    rank_html = _render_rank_table(corpus_id, rank_by)
    peer_html = _render_peer_cards(corpus_id)

    total = float(
        session.portco_profiles_df["total_projected_impact_usd_annual"].sum()
    )
    ensure_output_dirs()
    safe_id = "".join(
        c if c.isalnum() or c in "-_" else "_" for c in corpus_id
    )
    filename = output_filename or f"bx_report_{safe_id}.html"
    if not filename.endswith(".html"):
        filename += ".html"
    out_path = os.path.abspath(os.path.join(SCRIPT_DIR, filename))
    json_path = out_path.replace(".html", ".json")

    html_str = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cross-Portco Benchmark — {html.escape(corpus_id)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="eyebrow">Cross-Portco Benchmark</div>
  <h1><em>{session.portco_count}</em> portcos · <em>{_fmt_usd(total)}/yr</em> identified</h1>
  <p class="sub">LP-reportable benchmark across the fund's active Decision-Optimization Diagnostic runs.</p>

  <div class="meta">
    <div class="meta-item"><div class="meta-label">Corpus</div><div class="meta-value">{html.escape(corpus_id)}</div></div>
    <div class="meta-item"><div class="meta-label">Portfolio companies</div><div class="meta-value">{session.portco_count}</div></div>
    <div class="meta-item"><div class="meta-label">Total identified</div><div class="meta-value good">{_fmt_usd(total)}/yr</div></div>
    <div class="meta-item"><div class="meta-label">Opportunities</div><div class="meta-value">{len(session.opportunities_df)}</div></div>
    <div class="meta-item"><div class="meta-label">As of</div><div class="meta-value">{datetime.now(timezone.utc).date().isoformat()}</div></div>
  </div>

  {arche_html}
  {rank_html}
  {peer_html}

  <div class="disclaimer">
    For educational/informational purposes only. Not financial advice.
    Benchmark statistics derive from Decision-Optimization Diagnostic outputs
    and reflect modeled opportunities, not realized results.
  </div>

  <div class="footer">
    <span>Generated by Cross-Portco Benchmark · Claude-native</span>
    <span>{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</span>
  </div>
</div>
</body>
</html>
"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_str)

    # JSON sidecar — rank table + archetype index in one payload
    sidecar = {
        "corpus_id": corpus_id,
        "portco_count": session.portco_count,
        "total_identified_usd_annual": round(total, 2),
        "archetype_index": index,
        "rank_table": [
            bx_portco_rank(corpus_id, pid, rank_by)
            for pid in session.portco_profiles_df["portco_id"].tolist()
        ],
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(sidecar, f, indent=2, default=str)

    return {
        "path": out_path,
        "json_path": json_path,
        "bytes_written": len(html_str.encode("utf-8")),
        "portco_count": session.portco_count,
    }
