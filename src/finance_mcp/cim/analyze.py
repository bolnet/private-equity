"""
cim_analyze — Top-level CIM red-flag extractor.

Entry point glues fetcher (SEC EDGAR) → parser (HTML → sections) → flags
(8 heuristic extractors) → report (editorial-letterpress HTML).

Two modes:
  - by ticker: tool resolves ticker → CIK → latest 10-K → fetches → analyzes
  - by local path: tool reads a pre-downloaded HTML and analyzes

Real-data demo: Sotera Health (SHC) FY2025 10-K filed 2026-02-24.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Optional

from fastmcp.exceptions import ToolError

from finance_mcp.cim.fetcher import Filing, download, latest_form
from finance_mcp.cim.flags import Flag, extract_flags, summarize_flags
from finance_mcp.cim.parser import parse_10k, section_label


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Diligence red-flag report — {company_name}</title>
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
    --accent-2: #93331f;
    --high:     #6b1414;
    --med:      #93331f;
    --low:      #8a6f1a;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  html {{ background: #ece4cb; }}
  body {{
    background:
      radial-gradient(ellipse 1200px 800px at 50% -100px, rgba(255,248,220,0.6), transparent 70%),
      var(--paper);
    color: var(--ink);
    font-family: 'EB Garamond', serif;
    font-size: 17px;
    line-height: 1.6;
    font-feature-settings: "liga", "dlig", "onum", "kern";
    -webkit-font-smoothing: antialiased;
    padding: 64px 20px 88px;
  }}
  body::before {{
    content: ''; position: fixed; inset: 0; pointer-events: none; z-index: 0; mix-blend-mode: multiply; opacity: 0.35;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0.42  0 0 0 0 0.34  0 0 0 0 0.18  0 0 0 0.07 0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>");
  }}
  .sheet {{
    max-width: 780px; margin: 0 auto; background: var(--page);
    position: relative; z-index: 1;
    padding: 80px 70px 64px;
    box-shadow: 0 1px 0 var(--rule-soft), 0 30px 60px -30px rgba(60,40,15,0.18);
    border: 1px solid rgba(194, 173, 132, 0.45);
  }}
  .letterhead {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 48px; gap: 24px; }}
  .wordmark {{ display: flex; align-items: center; gap: 12px; font-family: 'Cormorant Garamond', serif; font-style: italic; font-size: 21px; color: var(--ink); }}
  .wordmark .seal {{ width: 34px; height: 34px; border-radius: 50%; border: 1px solid var(--accent); color: var(--accent); display: inline-flex; align-items: center; justify-content: center; font-size: 16px; font-weight: 500; background: rgba(107,20,20,0.04); }}
  .meta {{ text-align: right; font-family: 'Newsreader', serif; font-style: italic; font-weight: 300; font-size: 13px; color: var(--ink-faint); line-height: 1.5; }}
  .meta strong {{ display: block; font-style: normal; font-variant: small-caps; letter-spacing: 0.14em; font-size: 11px; color: var(--ink-dim); margin-bottom: 2px; }}
  .eyebrow {{ font-variant: small-caps; letter-spacing: 0.18em; font-size: 12px; color: var(--accent); font-weight: 600; margin-bottom: 14px; display: inline-block; }}
  .eyebrow::before {{ content: '— '; color: var(--rule); }}
  .eyebrow::after {{ content: ' —'; color: var(--rule); }}
  h1 {{ font-family: 'Cormorant Garamond', serif; font-weight: 400; font-size: 46px; line-height: 1.06; margin: 0 0 18px; }}
  h1 em {{ font-style: italic; color: var(--accent); }}
  .lede {{ font-style: italic; color: var(--ink-dim); font-size: 18px; margin: 0 0 32px; max-width: 56ch; }}
  .stats-strip {{ display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule); padding: 18px 0; margin: 28px 0 44px; }}
  .stats-strip .stat {{ text-align: center; padding: 0 14px; border-right: 1px solid var(--rule-soft); }}
  .stats-strip .stat:last-child {{ border-right: none; }}
  .stats-strip .stat-label {{ font-variant: small-caps; letter-spacing: 0.16em; font-size: 11px; color: var(--ink-faint); margin-bottom: 6px; }}
  .stats-strip .stat-num {{ font-family: 'Cormorant Garamond', serif; font-weight: 500; font-size: 26px; color: var(--ink); }}
  .stats-strip .stat-num.high {{ color: var(--high); }}
  .stats-strip .stat-num.med {{ color: var(--med); }}
  .stats-strip .stat-num.low {{ color: var(--low); }}

  .summary {{ border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule); padding: 24px 0; margin: 24px 0 36px; }}
  .summary-label {{ font-variant: small-caps; letter-spacing: 0.18em; font-size: 12px; font-weight: 600; color: var(--accent); margin-bottom: 4px; }}
  .summary p {{ margin: 0 0 14px; font-size: 16.5px; }}
  .summary p:last-child {{ margin-bottom: 0; }}

  h2.section-head {{ font-family: 'Cormorant Garamond', serif; font-weight: 500; font-size: 30px; margin: 56px 0 18px; padding-bottom: 8px; border-bottom: 1px solid var(--rule); color: var(--ink); }}

  .flag {{ margin: 28px 0; padding: 22px 24px; border-left: 3px solid var(--accent); background: rgba(107, 20, 20, 0.02); position: relative; }}
  .flag.high {{ border-left-color: var(--high); }}
  .flag.medium {{ border-left-color: var(--med); background: rgba(147, 51, 31, 0.025); }}
  .flag.low {{ border-left-color: var(--low); background: rgba(138, 111, 26, 0.04); }}
  .flag-head {{ display: flex; align-items: baseline; gap: 14px; margin-bottom: 10px; flex-wrap: wrap; }}
  .flag-num {{ font-family: 'Cormorant Garamond', serif; font-style: italic; font-size: 22px; color: var(--accent); }}
  .flag-type {{ font-family: 'Cormorant Garamond', serif; font-weight: 500; font-size: 22px; color: var(--ink); flex: 1; min-width: 0; }}
  .flag-pill {{ font-variant: small-caps; letter-spacing: 0.14em; font-size: 11px; font-weight: 600; padding: 3px 10px; border-radius: 999px; }}
  .flag-pill.high {{ color: white; background: var(--high); }}
  .flag-pill.medium {{ color: white; background: var(--med); }}
  .flag-pill.low {{ color: white; background: var(--low); }}
  .flag-citation {{ font-family: 'Newsreader', serif; font-style: italic; font-size: 13px; color: var(--ink-faint); margin-bottom: 14px; }}
  .flag-citation code {{ font-family: 'JetBrains Mono', monospace; font-style: normal; font-size: 12px; background: rgba(194, 173, 132, 0.2); padding: 1px 6px; border-radius: 2px; }}
  .flag-excerpt {{ font-family: 'EB Garamond', serif; font-style: italic; font-size: 16px; color: var(--ink-dim); margin: 0 0 14px; padding: 12px 16px; border-left: 1px solid var(--rule); background: rgba(255, 247, 215, 0.6); }}
  .flag-excerpt::before {{ content: '“'; font-family: 'Cormorant Garamond', serif; font-size: 28px; color: var(--accent); margin-right: 4px; vertical-align: -8px; }}
  .flag-excerpt::after {{ content: '”'; font-family: 'Cormorant Garamond', serif; font-size: 28px; color: var(--accent); margin-left: 4px; vertical-align: -8px; }}
  .flag-rationale {{ font-size: 15px; color: var(--ink-dim); margin: 0; }}
  .flag-rationale strong {{ color: var(--ink); font-weight: 600; }}

  .colophon {{ margin-top: 64px; padding-top: 24px; border-top: 1px solid var(--rule); font-family: 'Newsreader', serif; font-style: italic; font-weight: 300; font-size: 12px; color: var(--ink-faint); text-align: center; line-height: 1.6; }}
  .colophon code {{ font-family: 'JetBrains Mono', monospace; font-style: normal; font-size: 11px; color: var(--ink-dim); background: rgba(194, 173, 132, 0.18); padding: 1px 6px; border-radius: 2px; }}
  .colophon a {{ color: var(--accent); text-decoration: none; border-bottom: 1px solid var(--rule-soft); }}
  .colophon .signature {{ font-family: 'Cormorant Garamond', serif; font-style: italic; font-size: 16px; color: var(--ink-dim); margin-bottom: 10px; }}
  .colophon .signature::before {{ content: ''; display: block; width: 110px; height: 1px; background: var(--ink-faint); opacity: 0.5; margin: 0 auto 10px; }}

  @media (max-width: 720px) {{ body {{ padding: 28px 8px 56px; }} .sheet {{ padding: 44px 24px 48px; }} h1 {{ font-size: 32px; }} .stats-strip {{ grid-template-columns: repeat(2, 1fr); gap: 12px; }} .stats-strip .stat {{ border-right: none; border-bottom: 1px dotted var(--rule-soft); padding-bottom: 10px; }} .letterhead {{ flex-direction: column; gap: 8px; }} .meta {{ text-align: left; }} }}
</style>
</head>
<body>
<article class="sheet">
  <header class="letterhead">
    <div class="wordmark"><span class="seal">⚑</span><span>Diligence Red-Flag Report</span></div>
    <div class="meta">
      <strong>Source filing</strong>
      {form} &middot; {company_name}<br />
      Filed {filing_date} &middot; {fiscal_year_end}
    </div>
  </header>

  <div class="eyebrow">Read in {char_count_kb}K characters of disclosure</div>
  <h1>{n_flags} red flags surfaced<br /><em>across {n_items} sections.</em></h1>
  <p class="lede">A heuristic pass over a public regulatory disclosure, calibrated for PE diligence priorities. Every flag carries a section + paragraph citation; severity is heuristic.</p>

  <div class="stats-strip">
    <div class="stat"><div class="stat-label">Total flags</div><div class="stat-num">{n_flags}</div></div>
    <div class="stat"><div class="stat-label">High severity</div><div class="stat-num high">{n_high}</div></div>
    <div class="stat"><div class="stat-label">Medium</div><div class="stat-num med">{n_med}</div></div>
    <div class="stat"><div class="stat-label">Low</div><div class="stat-num low">{n_low}</div></div>
  </div>

  <div class="summary">
    <div class="summary-label">Headline</div>
    <p>{headline_summary}</p>
    <div class="summary-label" style="margin-top: 14px;">Distribution</div>
    <p>{type_distribution}</p>
  </div>

  <h2 class="section-head">Flags, ordered by severity</h2>
  {flag_blocks}

  <footer class="colophon">
    <div class="signature">Composed at the disclosure layer.</div>
    Generated by <code>cim_analyze</code> from a public SEC filing.
    Source URL: <a href="{source_url}" target="_blank" rel="noopener">{source_url}</a><br />
    {n_flags} flags &middot; 8 heuristic extractors &middot; every excerpt traces to its source paragraph &middot; generated {as_of}.
  </footer>
</article>
</body>
</html>
"""


_FLAG_BLOCK = """\
<div class="flag {severity}">
  <div class="flag-head">
    <div class="flag-num">{idx}.</div>
    <div class="flag-type">{flag_label}</div>
    <div class="flag-pill {severity}">{severity}</div>
  </div>
  <div class="flag-citation">Item {item} &middot; {section_lbl} &middot; paragraph {paragraph_index} <code>{flag_type}</code></div>
  <div class="flag-excerpt">{excerpt}</div>
  <p class="flag-rationale"><strong>Why it's flagged.</strong> {rationale}</p>
</div>
"""


_FLAG_TYPE_LABELS = {
    "customer_concentration": "Customer / portfolio concentration",
    "going_concern": "Going-concern language",
    "material_weakness": "Material weakness in internal control",
    "goodwill_impairment": "Goodwill impairment",
    "auditor_change": "Auditor change",
    "related_party": "Related-party transactions",
    "restatement": "Prior-period restatement",
    "severe_risk_factor": "Self-disclosed material risk factor",
}


def _format_type_distribution(by_type: dict[str, int]) -> str:
    """Human-readable type distribution sentence."""
    if not by_type:
        return "No flags surfaced."
    parts = [f"{n} {_FLAG_TYPE_LABELS.get(t, t)}" for t, n in by_type.items()]
    if len(parts) == 1:
        return parts[0] + "."
    return ", ".join(parts[:-1]) + f", and {parts[-1]}."


def _format_headline(summary: dict, company: str) -> str:
    n = summary["total"]
    sev = summary["by_severity"]
    if n == 0:
        return f"No diligence flags surfaced in this filing for {company}."
    return (
        f"A heuristic scan of the {company} filing surfaces {n} flags — "
        f"{sev['high']} high-severity, {sev['medium']} medium, {sev['low']} low. "
        "Each carries a section citation and a 1-2 sentence excerpt; a "
        "diligence reviewer can verify or dismiss every entry against the "
        "source disclosure."
    )


def cim_analyze(
    ticker: Optional[str] = None,
    local_html_path: Optional[str] = None,
    form: str = "10-K",
    output_filename: Optional[str] = None,
) -> dict:
    """
    Run the diligence red-flag extractor on a SEC filing.

    Modes:
        - Pass `ticker='SHC'` (or any other) to fetch the latest `form` from EDGAR.
        - Pass `local_html_path='/path/to/file.htm'` to analyze a pre-downloaded
          filing (no network call).

    Returns:
        dict with report_path, json_path, n_flags, by_severity, by_type, source_url.

    Raises:
        ToolError on missing input or empty parse.
    """
    if not ticker and not local_html_path:
        raise ToolError("cim_analyze requires either ticker= or local_html_path=.")

    filing: Optional[Filing] = None
    if ticker:
        filing = latest_form(ticker, form=form)
        local_path = download(filing)
    else:
        local_path = Path(local_html_path)
        if not local_path.exists():
            raise ToolError(f"Local HTML not found: {local_path}")

    parsed = parse_10k(local_path)
    if not parsed.sections:
        raise ToolError(
            f"No standard 10-K/S-1 sections found in {local_path}. "
            "The HTML may be malformed or the wrong document type."
        )

    flags = extract_flags(parsed)
    summary = summarize_flags(flags)

    company = (
        (filing.company_name if filing else "")
        or parsed.company_name
        or "Subject company"
    )
    fiscal_year_end = parsed.fiscal_year_end or "fiscal year"
    filing_date = filing.filing_date if filing else "n/a"
    source_url = filing.url if filing else f"file://{local_path.resolve()}"

    flag_blocks: list[str] = []
    for i, f in enumerate(flags, start=1):
        flag_blocks.append(
            _FLAG_BLOCK.format(
                idx=i,
                flag_label=_FLAG_TYPE_LABELS.get(f.flag_type, f.flag_type),
                severity=f.severity,
                item=f.item,
                section_lbl=section_label(f.item),
                paragraph_index=f.paragraph_index,
                flag_type=f.flag_type,
                excerpt=f.excerpt.replace("<", "&lt;").replace(">", "&gt;"),
                rationale=f.rationale,
            )
        )

    html = _HTML_TEMPLATE.format(
        company_name=company,
        form=form,
        filing_date=filing_date,
        fiscal_year_end=fiscal_year_end,
        char_count_kb=parsed.char_count // 1024,
        n_flags=summary["total"],
        n_items=len(parsed.sections),
        n_high=summary["by_severity"]["high"],
        n_med=summary["by_severity"]["medium"],
        n_low=summary["by_severity"]["low"],
        headline_summary=_format_headline(summary, company),
        type_distribution=_format_type_distribution(summary["by_type"]),
        flag_blocks="\n".join(flag_blocks) if flag_blocks else "<p>No flags surfaced.</p>",
        source_url=source_url,
        as_of=date.today().isoformat(),
    )

    out_dir = Path("finance_output")
    out_dir.mkdir(parents=True, exist_ok=True)
    base = ticker or local_path.stem
    out_name = output_filename or f"cim_redflags_{base}.html"
    out_path = out_dir / out_name
    out_path.write_text(html, encoding="utf-8")

    json_out = out_path.with_suffix(".json")
    json_out.write_text(
        json.dumps(
            {
                "company_name": company,
                "form": form,
                "filing_date": filing_date,
                "fiscal_year_end": fiscal_year_end,
                "source_url": source_url,
                "char_count": parsed.char_count,
                "n_sections_parsed": len(parsed.sections),
                "summary": summary,
                "flags": [asdict(f) for f in flags],
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    return {
        "report_path": str(out_path),
        "json_path": str(json_out),
        "n_flags": summary["total"],
        "by_severity": summary["by_severity"],
        "by_type": summary["by_type"],
        "source_url": source_url,
        "company_name": company,
    }
