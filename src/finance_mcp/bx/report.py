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
  --paper:#f4ecd5; --page:#fbf6e2; --page-dim:#f6efd7;
  --ink:#1a140d; --ink-mid:#3a2e1d; --ink-dim:#5a4a35; --ink-faint:#8b765a;
  --rule:#c2ad84; --rule-soft:#dfd2af; --rule-mute:#e8dfc0;
  --accent:#6b1414; --accent-2:#93331f; --gold:#8a6f1a;
  --good:#4d6b2c; --red:#8b2a2a; --amber:#b35a1f; --blue:#3d5a8a;
}
*{margin:0;padding:0;box-sizing:border-box}
html{background:#ece4cb}
body{
  font-family:'EB Garamond','Iowan Old Style',Georgia,serif;
  background:
    radial-gradient(ellipse 1100px 700px at 50% -100px, rgba(255,248,220,.55), transparent 70%),
    radial-gradient(ellipse 600px 450px at 90% 40%, rgba(107,20,20,.025), transparent 60%),
    radial-gradient(ellipse 500px 500px at 5% 80%, rgba(138,111,26,.04), transparent 60%),
    var(--paper);
  color:var(--ink);
  font-size:17px; line-height:1.6;
  font-feature-settings:"liga","dlig","onum","kern";
  text-rendering:optimizeLegibility; -webkit-font-smoothing:antialiased;
  padding:64px 24px 96px; min-height:100vh;
}
body::before{
  content:''; position:fixed; inset:0;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='240' height='240'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0.42  0 0 0 0 0.34  0 0 0 0 0.18  0 0 0 0.07 0'/></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>");
  opacity:.42; mix-blend-mode:multiply;
  pointer-events:none; z-index:0;
}

.wrap{
  max-width:1200px; margin:0 auto;
  position:relative; z-index:1;
  background:var(--page);
  padding:64px 64px 56px;
  border:1px solid rgba(194,173,132,.45);
  box-shadow:
    0 1px 0 var(--rule-soft),
    0 30px 60px -30px rgba(60,40,15,.18),
    0 8px 18px -6px rgba(60,40,15,.08);
}

.eyebrow{
  font-family:'Cormorant Garamond',serif;
  font-style:italic; font-weight:400;
  font-size:13px; color:var(--accent);
  letter-spacing:.28em; text-transform:uppercase; margin-bottom:16px;
}
.eyebrow::before{content:'— '; color:var(--rule); letter-spacing:normal}
.eyebrow::after {content:' —'; color:var(--rule); letter-spacing:normal}
h1{
  font-family:'Cormorant Garamond',serif;
  font-weight:500; font-size:46px; line-height:1.1;
  letter-spacing:-.005em; color:var(--ink);
}
h1 em{font-style:italic; color:var(--accent); font-weight:500}
.sub{
  font-family:'EB Garamond',serif; font-style:italic;
  color:var(--ink-dim); margin-top:14px; font-size:18px;
  max-width:62ch;
}
.meta{
  display:flex; flex-wrap:wrap; gap:0;
  margin-top:32px;
  border-top:1px solid var(--rule);
  border-bottom:1px solid var(--rule);
  padding:18px 0; background:transparent;
}
.meta-item{
  flex:1 1 auto; min-width:140px;
  padding:0 22px; display:flex; flex-direction:column; gap:6px;
  border-right:1px solid var(--rule-soft);
}
.meta-item:last-child{border-right:none}
.meta-label{
  font-variant:small-caps; letter-spacing:.16em;
  font-size:11px; color:var(--ink-faint); font-weight:500;
}
.meta-value{
  font-family:'Cormorant Garamond',serif;
  font-weight:500; font-size:22px; color:var(--ink);
  font-feature-settings:"lnum","tnum"; line-height:1.1;
}
.meta-value.good{color:var(--accent)}

.section{margin-top:56px}
.section h2{
  font-family:'Cormorant Garamond',serif;
  font-size:30px; font-weight:500; margin-bottom:6px;
  letter-spacing:-.005em; color:var(--ink);
}
.section .sub-h{
  font-family:'EB Garamond',serif; font-style:italic;
  color:var(--ink-dim); font-size:15px; margin-bottom:22px;
  border-bottom:1px solid var(--rule); padding-bottom:14px;
}

/* Archetype index — letterpress bands */
.arche-grid{display:grid; gap:10px}
.arche-row{
  display:grid; grid-template-columns:160px 1fr 110px;
  gap:18px; align-items:center; padding:14px 18px;
  background:var(--page-dim);
  border:1px solid var(--rule-soft);
}
.arche-name{
  font-family:'Cormorant Garamond',serif;
  font-weight:500; font-size:17px;
  color:var(--ink); text-transform:capitalize;
}
.arche-range{
  position:relative; height:26px;
  background:var(--rule-mute);
  border:1px solid var(--rule-soft);
}
.arche-range .band{position:absolute; top:0; height:100%;
  background:var(--accent); opacity:.32}
.arche-range .median{position:absolute; top:-4px; bottom:-4px;
  width:2px; background:var(--accent)}
.arche-range .label{position:absolute; top:7px; right:10px;
  font-size:11px; color:var(--ink-faint); font-style:italic}
.arche-total{
  font-family:'Cormorant Garamond',serif; font-style:italic;
  font-feature-settings:"lnum","tnum"; font-size:18px;
  font-weight:500; text-align:right; color:var(--ink);
}

/* Rank table — gazette ledger */
.rank-tbl{
  width:100%; border-collapse:collapse;
  font-feature-settings:"lnum","tnum";
}
.rank-tbl th{
  text-align:left; font-weight:500;
  font-variant:small-caps; letter-spacing:.16em;
  font-size:11px; color:var(--ink-faint);
  padding:12px 14px;
  border-bottom:1px solid var(--rule);
}
.rank-tbl td{
  padding:16px 14px; border-bottom:1px solid var(--rule-soft);
  font-size:15px; color:var(--ink);
}
.rank-tbl tr:last-child td{border-bottom:none}
.rank-tbl tr:hover td{background:var(--page-dim)}
.rank-cell{
  color:var(--accent); font-weight:500;
  font-family:'Cormorant Garamond',serif; font-style:italic;
  font-size:17px;
}
.pct-cell{color:var(--ink-dim); font-size:14px; font-style:italic}
.neg{color:var(--red)}

/* Peer groups — letter cards */
.peer-card{
  padding:18px 22px; background:var(--page-dim);
  border:1px solid var(--rule-soft); margin-bottom:14px;
}
.peer-head{display:flex; justify-content:space-between; align-items:baseline;
  margin-bottom:12px; padding-bottom:8px;
  border-bottom:1px solid var(--rule-soft)}
.peer-portco{
  font-family:'Cormorant Garamond',serif;
  font-weight:500; font-size:19px; color:var(--ink);
}
.peer-tag{
  font-variant:small-caps; letter-spacing:.14em;
  font-size:11px; color:var(--blue); font-weight:600;
}
.peer-row{
  display:grid; grid-template-columns:1fr auto auto;
  gap:14px; padding:7px 0; font-size:14px;
  color:var(--ink-dim); border-bottom:1px dotted var(--rule-mute);
}
.peer-row:last-child{border-bottom:none}
.peer-row .name{color:var(--ink); font-style:italic}
.peer-row .score{
  font-family:'Cormorant Garamond',serif; font-weight:500;
  font-feature-settings:"lnum","tnum"; color:var(--ink);
}

.footer{
  margin-top:72px; padding-top:24px;
  border-top:1px solid var(--rule);
  color:var(--ink-faint); font-size:12px; font-style:italic;
  display:flex; justify-content:space-between;
}
.disclaimer{
  margin-top:36px; padding:14px 18px;
  background:var(--page-dim); border-left:2px solid var(--rule);
  color:var(--ink-faint); font-size:13px; font-style:italic;
  line-height:1.55;
}
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
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500;1,600&family=EB+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&family=Newsreader:ital,wght@0,300;0,400;1,300;1,400&display=swap" rel="stylesheet">
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
