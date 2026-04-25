"""
dx_report — Render the OpportunityMap as a self-contained static HTML file.

Reuses the Claude Finance design tokens (DM Sans + dark palette) from
docs/index.html. No server, no JS framework. Double-click to open.

The input is a plain dict (an OpportunityMap assembled by Claude), not a
dataclass, so this tool is callable from the MCP surface without the
agent having to construct Python objects.
"""
from __future__ import annotations

import base64
import html
import io
import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastmcp.exceptions import ToolError

from finance_mcp.dx.memo import dx_memo
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
.wrap{max-width:1120px; margin:0 auto}
.eyebrow{
  color:var(--accent); font-size:12px; font-weight:500;
  letter-spacing:.12em; text-transform:uppercase; margin-bottom:12px;
}
h1{font-size:36px; font-weight:500; letter-spacing:-.02em; line-height:1.15}
h1 em{font-style:normal; color:var(--accent)}
.sub{color:var(--text-secondary); margin-top:12px; font-size:16px}
.meta{
  display:flex; flex-wrap:wrap; gap:24px; margin-top:24px;
  padding:16px 20px; background:var(--bg-card);
  border:1px solid var(--border-subtle); border-radius:12px;
}
.meta-item{display:flex; flex-direction:column; gap:4px}
.meta-label{font-size:11px; color:var(--text-muted); letter-spacing:.08em;
  text-transform:uppercase}
.meta-value{font-size:18px; font-weight:500}
.meta-value.good{color:var(--accent)}
.meta-value.warn{color:var(--amber)}
.meta-value.bad{color:var(--red)}

.section{margin-top:48px}
.section h2{
  font-size:22px; font-weight:500; margin-bottom:16px;
  letter-spacing:-.01em;
}

/* Opportunity cards */
.opp{
  background:var(--bg-card); border:1px solid var(--border-subtle);
  border-radius:14px; padding:24px 28px; margin-bottom:16px;
  transition:background .18s ease;
}
.opp:hover{background:var(--bg-card-hover)}
.opp-head{
  display:flex; justify-content:space-between; align-items:flex-start;
  gap:24px; margin-bottom:12px;
}
.opp-title{font-size:17px; font-weight:500}
.opp-rank{
  font-size:11px; color:var(--text-muted); letter-spacing:.12em;
  text-transform:uppercase; margin-bottom:6px;
}
.opp-impact{
  font-size:24px; font-weight:500; color:var(--accent);
  font-variant-numeric:tabular-nums; white-space:nowrap;
}
.opp-impact.neg{color:var(--red)}
.opp-row{display:flex; flex-wrap:wrap; gap:12px 28px; color:var(--text-secondary);
  font-size:13px; margin-top:10px}
.opp-row strong{color:var(--text-primary); font-weight:500}
.opp-narr{
  margin-top:16px; padding:16px 18px;
  background:var(--bg-secondary); border-radius:10px;
  border-left:2px solid var(--accent); font-size:14px;
  color:var(--text-secondary);
}
.opp-narr h4{
  color:var(--text-primary); font-size:13px; font-weight:600;
  margin:12px 0 6px 0; letter-spacing:.03em;
}
.opp-narr h4:first-child{margin-top:0}
.opp-narr h5{
  color:var(--text-primary); font-size:12px; font-weight:600;
  margin:8px 0 4px 0; letter-spacing:.02em; text-transform:none;
}
.opp-narr p{margin-bottom:8px}
.opp-narr p:last-child{margin-bottom:0}
.memo-body{margin-bottom:8px}
.memo-violations{
  margin-top:10px; padding:10px 12px; background:rgba(255,77,106,0.08);
  border:1px solid rgba(255,77,106,0.3); border-radius:8px;
  color:var(--red); font-size:12px;
}

/* Executive Summary */
.exec-summary{
  margin-top:40px; padding:28px 32px;
  background:linear-gradient(180deg, var(--bg-card) 0%, var(--bg-secondary) 100%);
  border:1px solid var(--border); border-radius:16px;
}
.exec-eyebrow{
  color:var(--accent); font-size:11px; font-weight:600;
  letter-spacing:.14em; text-transform:uppercase; margin-bottom:10px;
}
.exec-headline{
  font-size:30px; font-weight:500; line-height:1.2; letter-spacing:-.01em;
  margin-bottom:8px;
}
.exec-headline em{font-style:normal; color:var(--accent)}
.exec-sub{color:var(--text-secondary); font-size:14px; margin-bottom:24px}

.exec-top3{display:grid; grid-template-columns:1fr; gap:8px; margin-bottom:24px}
.exec-top3-row{
  display:grid; grid-template-columns:32px 1fr auto auto; gap:16px;
  align-items:center; padding:12px 16px;
  background:var(--bg-primary); border:1px solid var(--border-subtle);
  border-radius:10px;
}
.exec-rank{
  color:var(--accent); font-weight:600; font-size:13px;
  font-variant-numeric:tabular-nums;
}
.exec-label{font-size:14px; color:var(--text-primary)}
.exec-impact{
  font-weight:500; font-size:15px; color:var(--accent);
  font-variant-numeric:tabular-nums; white-space:nowrap;
}
.exec-impact.neg{color:var(--red)}
.exec-eta{
  font-size:12px; color:var(--text-muted);
  font-variant-numeric:tabular-nums;
}

/* EBITDA bridge — pure CSS */
.bridge{
  margin-top:8px; padding:20px; background:var(--bg-primary);
  border:1px solid var(--border-subtle); border-radius:10px;
}
.bridge-title{
  color:var(--text-muted); font-size:11px; letter-spacing:.08em;
  text-transform:uppercase; margin-bottom:14px;
}
.bridge-row{
  display:grid; grid-template-columns:140px 1fr 120px; gap:12px;
  align-items:center; margin-bottom:10px;
}
.bridge-row:last-child{margin-bottom:0}
.bridge-label{font-size:13px; color:var(--text-secondary)}
.bridge-bar{
  height:22px; border-radius:4px;
  background:var(--border-subtle);
  position:relative; overflow:hidden;
}
.bridge-fill{
  height:100%; background:var(--accent); border-radius:4px;
  transition:width .3s ease;
}
.bridge-fill.baseline{background:var(--text-muted)}
.bridge-fill.top3{background:var(--amber)}
.bridge-fill.all{background:var(--accent)}
.bridge-value{
  text-align:right; font-size:13px; font-weight:500;
  font-variant-numeric:tabular-nums; color:var(--text-primary);
}

/* Per-opportunity quarterly chart */
.quarter-chart{
  margin-top:14px; padding:12px 14px; background:var(--bg-secondary);
  border:1px solid var(--border-subtle); border-radius:8px;
  text-align:center;
}
.quarter-chart img{max-width:100%; height:auto; display:block; margin:0 auto}
.quarter-chart-label{
  color:var(--text-muted); font-size:11px; letter-spacing:.06em;
  text-transform:uppercase; margin-bottom:6px;
}
.tag{
  display:inline-block; padding:2px 10px; background:var(--border-subtle);
  border:1px solid var(--border); border-radius:999px;
  font-size:11px; color:var(--text-secondary); letter-spacing:.03em;
  margin-right:6px;
}
.tag.archetype{color:var(--blue)}
.tag.diff-1,.tag.diff-2{color:var(--accent)}
.tag.diff-3{color:var(--amber)}
.tag.diff-4,.tag.diff-5{color:var(--red)}

.footer{
  margin-top:64px; padding-top:24px; border-top:1px solid var(--border-subtle);
  color:var(--text-muted); font-size:12px; display:flex; justify-content:space-between;
}
.disclaimer{
  margin-top:32px; padding:12px 16px; background:var(--bg-card);
  border:1px solid var(--border-subtle); border-radius:8px;
  color:var(--text-muted); font-size:12px; font-style:italic;
}
"""


def _fmt_usd(value: float) -> str:
    """Pretty-print a USD value at the appropriate scale."""
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


def _fmt_segment(segment: dict[str, Any]) -> str:
    """Render a segment dict as human-readable tags."""
    parts = []
    for k, v in segment.items():
        parts.append(
            f'<span class="tag">{html.escape(str(k))}='
            f"{html.escape(str(v))}</span>"
        )
    return "".join(parts)


def _render_quarterly_chart_base64(
    quarters: list[str],
    values: list[float],
    width_in: float = 4.8,
    height_in: float = 1.3,
) -> Optional[str]:
    """
    Render a small per-opportunity bar chart as a base64 data URL.

    Embedding inline keeps the final HTML self-contained (one file,
    emailable). Returns None if matplotlib isn't importable or the
    input is empty — report layer renders without the chart in that case.
    """
    if not quarters or not values or len(quarters) != len(values):
        return None
    try:
        # Import via finance_mcp.output to guarantee the Agg backend is set
        from finance_mcp.output import plt  # noqa: F401
        import matplotlib.pyplot as plt
    except Exception:
        return None

    fig, ax = plt.subplots(figsize=(width_in, height_in))
    colors = ["#ff4d6a" if v < 0 else "#00d4aa" for v in values]
    ax.bar(range(len(values)), values, color=colors, edgecolor="none")
    ax.axhline(0, color="#5c5c66", linewidth=0.8)
    ax.set_xticks(range(len(values)))
    ax.set_xticklabels(quarters, fontsize=7, color="#94949e", rotation=0)
    ax.tick_params(axis="y", colors="#94949e", labelsize=7)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_facecolor("#111113")
    fig.patch.set_facecolor("#111113")
    fig.tight_layout(pad=0.2)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, transparent=False)
    plt.close(fig)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _render_exec_summary(opportunity_map: dict[str, Any]) -> str:
    """Top-of-report executive summary: headline, top-3 list, EBITDA bridge."""
    portco_id = str(opportunity_map.get("portco_id", "unknown"))
    ebitda_baseline = float(opportunity_map.get("ebitda_baseline_usd", 0.0))
    total_impact = float(
        opportunity_map.get("total_projected_impact_usd_annual", 0.0)
    )
    opps = list(opportunity_map.get("opportunities", []))
    if not opps:
        return ""

    pct_of_ebitda = (total_impact / ebitda_baseline * 100) if ebitda_baseline else 0.0
    impact_class = "neg" if total_impact < 0 else ""

    # Top-3 block
    top3 = opps[:3]
    top3_impact = sum(
        float(o.get("projected_impact_usd_annual", 0.0)) for o in top3
    )
    coverage_pct = (top3_impact / total_impact * 100) if total_impact else 0.0

    top3_rows = []
    for i, o in enumerate(top3):
        rec = str(o.get("recommendation") or o.get("id") or f"Opportunity #{i+1}")
        imp = float(o.get("projected_impact_usd_annual", 0.0))
        imp_cls = "neg" if imp < 0 else ""
        weeks = int(o.get("time_to_implement_weeks", 0) or 0)
        top3_rows.append(
            f"""
            <div class="exec-top3-row">
              <div class="exec-rank">{i+1:02d}</div>
              <div class="exec-label">{html.escape(rec)}</div>
              <div class="exec-impact {imp_cls}">{_fmt_usd(imp)}/yr</div>
              <div class="exec-eta">{weeks} wk{"s" if weeks != 1 else ""}</div>
            </div>
            """
        )

    # EBITDA bridge — three bars sharing the same scale.
    # Scale reference = max of (|baseline|, |baseline + total_impact|)
    post_all = ebitda_baseline + total_impact
    post_top3 = ebitda_baseline + top3_impact
    scale_ref = max(
        abs(ebitda_baseline), abs(post_top3), abs(post_all), 1.0
    )

    def _bar(value: float, css_class: str) -> str:
        pct = min(100.0, abs(value) / scale_ref * 100.0)
        return (
            f'<div class="bridge-bar">'
            f'<div class="bridge-fill {css_class}" style="width:{pct:.1f}%"></div>'
            f'</div>'
        )

    bridge_html = f"""
    <div class="bridge">
      <div class="bridge-title">EBITDA Bridge (Current → Projected)</div>
      <div class="bridge-row">
        <div class="bridge-label">Current baseline</div>
        {_bar(ebitda_baseline, "baseline")}
        <div class="bridge-value">{_fmt_usd(ebitda_baseline)}</div>
      </div>
      <div class="bridge-row">
        <div class="bridge-label">After top-3 ({len(top3)} opps)</div>
        {_bar(post_top3, "top3")}
        <div class="bridge-value">{_fmt_usd(post_top3)}</div>
      </div>
      <div class="bridge-row">
        <div class="bridge-label">After all {len(opps)} opps</div>
        {_bar(post_all, "all")}
        <div class="bridge-value">{_fmt_usd(post_all)}</div>
      </div>
    </div>
    """

    return f"""
    <div class="exec-summary">
      <div class="exec-eyebrow">Executive Summary</div>
      <div class="exec-headline">
        <em>{_fmt_usd(total_impact)}/yr</em> identified · {pct_of_ebitda:+.1f}% vs EBITDA baseline
      </div>
      <div class="exec-sub">
        Top 3 account for {coverage_pct:.0f}% of the identified gap ({_fmt_usd(top3_impact)}/yr).
        {len(opps)} total opportunities reviewed.
      </div>
      <div class="exec-top3">
        {''.join(top3_rows)}
      </div>
      {bridge_html}
    </div>
    """


def _narrative_to_html(narrative: str) -> str:
    """Convert a markdown-ish memo into minimal safe HTML.
    Supports: '### heading' -> <h5>, blank line -> paragraph break.
    All other content is escaped."""
    out: list[str] = []
    paragraph: list[str] = []

    def flush():
        if paragraph:
            text = " ".join(paragraph).strip()
            if text:
                out.append(f"<p>{html.escape(text)}</p>")
            paragraph.clear()

    for line in narrative.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if stripped.startswith("### "):
            flush()
            out.append(f"<h5>{html.escape(stripped[4:])}</h5>")
        elif stripped.startswith("## "):
            flush()
            out.append(f"<h4>{html.escape(stripped[3:])}</h4>")
        else:
            paragraph.append(stripped)
    flush()
    return "".join(out)


def _render_opportunity(idx: int, opp: dict[str, Any]) -> str:
    impact = float(opp.get("projected_impact_usd_annual", 0.0))
    impact_class = "neg" if impact < 0 else ""
    persistence = opp.get("persistence_quarters_out_of_total", [0, 0])
    persistence_str = f"{persistence[0]}/{persistence[1]}"
    difficulty = int(opp.get("difficulty_score_1_to_5", 3))
    archetype = str(opp.get("archetype", ""))
    recommendation = str(opp.get("recommendation", ""))

    # Run each narrative through dx_memo — fills in a deterministic skeleton
    # when the agent hasn't provided prose, and records violations.
    board = dx_memo(opp, audience="board")
    operator = dx_memo(opp, audience="operator")

    narr_html = ""
    narr_html += (
        f"<h4>Board view</h4>"
        f"<div class='memo-body'>{_narrative_to_html(board['formatted'])}</div>"
    )
    narr_html += (
        f"<h4>Operator view</h4>"
        f"<div class='memo-body'>{_narrative_to_html(operator['formatted'])}</div>"
    )

    all_violations = board["violations"] + operator["violations"]
    if all_violations:
        escaped = [html.escape(v) for v in all_violations]
        narr_html += (
            "<div class='memo-violations'><strong>Validation flags:</strong> "
            + "; ".join(escaped)
            + "</div>"
        )

    # Optional quarterly trend chart — rendered only when the Opportunity
    # carries quarterly data (populated by Claude from dx_time_stability).
    chart_html = ""
    quarters = list(opp.get("quarters") or [])
    values = list(opp.get("quarterly_outcome_total_usd") or [])
    if quarters and values:
        img_src = _render_quarterly_chart_base64(quarters, [float(v) for v in values])
        if img_src:
            chart_html = (
                '<div class="quarter-chart">'
                '<div class="quarter-chart-label">Quarterly outcome trend</div>'
                f'<img src="{img_src}" alt="Quarterly outcome trend" />'
                '</div>'
            )

    return f"""
    <div class="opp">
      <div class="opp-head">
        <div>
          <div class="opp-rank">Opportunity #{idx + 1}</div>
          <div class="opp-title">{html.escape(recommendation) or html.escape(str(opp.get("id", "")))}</div>
        </div>
        <div class="opp-impact {impact_class}">{_fmt_usd(impact)}/yr</div>
      </div>
      <div>
        <span class="tag archetype">{html.escape(archetype)}</span>
        {_fmt_segment(opp.get("segment", {}))}
        <span class="tag diff-{difficulty}">difficulty {difficulty}/5</span>
        <span class="tag">persistence {persistence_str}</span>
        <span class="tag">n={int(opp.get("n", 0)):,}</span>
      </div>
      <div class="opp-row">
        <div><strong>{_fmt_usd(opp.get("current_outcome_usd_annual", 0.0))}</strong> current annual outcome</div>
        <div><strong>{_fmt_usd(opp.get("projected_outcome_usd_annual", 0.0))}</strong> projected annual outcome</div>
        <div><strong>{int(opp.get("time_to_implement_weeks", 0))} weeks</strong> to implement</div>
      </div>
      {chart_html}
      {'<div class="opp-narr">' + narr_html + '</div>' if narr_html else ''}
    </div>
    """


def _render_html(opportunity_map: dict[str, Any]) -> str:
    portco_id = str(opportunity_map.get("portco_id", "unknown"))
    vertical = str(opportunity_map.get("vertical", "custom"))
    ebitda_baseline = float(opportunity_map.get("ebitda_baseline_usd", 0.0))
    total_impact = float(opportunity_map.get("total_projected_impact_usd_annual", 0.0))
    as_of = str(opportunity_map.get("as_of", datetime.now(timezone.utc).date().isoformat()))
    opps = list(opportunity_map.get("opportunities", []))

    pct_of_ebitda = (total_impact / ebitda_baseline * 100) if ebitda_baseline else 0.0
    impact_class = "good" if total_impact > 0 else ("bad" if total_impact < 0 else "warn")

    opp_html = "".join(_render_opportunity(i, o) for i, o in enumerate(opps))
    if not opp_html:
        opp_html = (
            '<div class="opp"><div class="opp-row">'
            "No opportunities met the filtering thresholds."
            "</div></div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Decision-Optimization Diagnostic — {html.escape(portco_id)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="eyebrow">Decision-Optimization Diagnostic</div>
  <h1>{html.escape(portco_id)} — <em>{_fmt_usd(total_impact)}/yr</em> of identified EBITDA opportunity.</h1>
  <p class="sub">Cross-section analysis across {len(opps)} decision opportunities, ranked by projected annual impact.</p>

  <div class="meta">
    <div class="meta-item">
      <div class="meta-label">Portco</div>
      <div class="meta-value">{html.escape(portco_id)}</div>
    </div>
    <div class="meta-item">
      <div class="meta-label">Vertical</div>
      <div class="meta-value">{html.escape(vertical)}</div>
    </div>
    <div class="meta-item">
      <div class="meta-label">EBITDA Baseline</div>
      <div class="meta-value">{_fmt_usd(ebitda_baseline)}</div>
    </div>
    <div class="meta-item">
      <div class="meta-label">Total Identified Opportunity</div>
      <div class="meta-value {impact_class}">{_fmt_usd(total_impact)}/yr</div>
    </div>
    <div class="meta-item">
      <div class="meta-label">As % of EBITDA</div>
      <div class="meta-value {impact_class}">{pct_of_ebitda:+.1f}%</div>
    </div>
    <div class="meta-item">
      <div class="meta-label">As of</div>
      <div class="meta-value">{html.escape(as_of)}</div>
    </div>
  </div>

  {_render_exec_summary(opportunity_map)}

  <div class="section">
    <h2>Ranked opportunity map</h2>
    {opp_html}
  </div>

  <div class="disclaimer">
    For educational/informational purposes only. Not financial advice.
    Projections are modeled from historical data and may not reflect future
    results. Review top-3 findings with an operator before acting.
  </div>

  <div class="footer">
    <span>Generated by Decision-Optimization Diagnostic · Claude-native</span>
    <span>{html.escape(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))}</span>
  </div>
</div>
</body>
</html>
"""


def dx_report(
    opportunity_map: dict,
    output_filename: str = "",
) -> dict:
    """
    Render an OpportunityMap as a self-contained static HTML file.

    Args:
        opportunity_map: Plain dict with keys portco_id, vertical,
            ebitda_baseline_usd, total_projected_impact_usd_annual,
            opportunities (list). Typically produced by the Claude agent
            after calling dx_ingest + dx_segment_stats + etc.
        output_filename: Optional filename (basename only, no directory).
            Defaults to 'dx_report_<portco_id>.html'.

    Returns:
        dict with path (absolute) + bytes_written + opportunities_rendered.
    """
    if not isinstance(opportunity_map, dict):
        raise ToolError("opportunity_map must be a dict.")

    ensure_output_dirs()
    portco_id = str(opportunity_map.get("portco_id", "unknown"))
    safe_portco = "".join(
        c if c.isalnum() or c in "-_" else "_" for c in portco_id
    )
    filename = output_filename or f"dx_report_{safe_portco}.html"
    if not filename.endswith(".html"):
        filename = filename + ".html"

    out_path = os.path.abspath(os.path.join(SCRIPT_DIR, filename))
    html_str = _render_html(opportunity_map)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_str)

    # Also dump a JSON sidecar for LP reporting pipelines
    json_path = out_path.replace(".html", ".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(opportunity_map, f, indent=2, default=str)

    return {
        "path": out_path,
        "json_path": json_path,
        "bytes_written": len(html_str.encode("utf-8")),
        "opportunities_rendered": len(opportunity_map.get("opportunities", [])),
    }
