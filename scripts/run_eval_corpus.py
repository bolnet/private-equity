"""
Run the eval harness across every explainer memo in finance_output/ and
render a corpus-level summary. Pure pandas + the existing eval_pe_output
tool — no LLM call.

Usage:
    python -m scripts.run_eval_corpus

Output:
    finance_output/eval_corpus_summary.html
    finance_output/eval_corpus_summary.json
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from finance_mcp.eval import eval_pe_output  # noqa: E402

OUT = REPO_ROOT / "finance_output"


def _scan_pairs() -> list[tuple[str, str, Path, Path]]:
    """Walk finance_output/ for explain_*_<audience>.json + matching dx_report_*.json."""
    pairs = []
    for memo_path in sorted(OUT.glob("explain_*.json")):
        # filename: explain_<portco_id>_<audience>.json
        stem = memo_path.stem  # explain_MortgageCo_board
        if not stem.startswith("explain_"):
            continue
        rest = stem[len("explain_"):]
        if rest.endswith("_board"):
            portco, audience = rest[:-len("_board")], "board"
        elif rest.endswith("_operator"):
            portco, audience = rest[:-len("_operator")], "operator"
        else:
            continue
        source = OUT / f"dx_report_{portco}.json"
        if source.exists():
            pairs.append((portco, audience, memo_path, source))
    return pairs


def main() -> None:
    pairs = _scan_pairs()
    print(f"[eval] found {len(pairs)} memo/source pairs")

    rows: list[dict] = []
    for portco, audience, memo, source in pairs:
        try:
            res = eval_pe_output(
                memo_json_path=str(memo),
                source_json_path=str(source),
            )
            scores = res.get("scores") or {}
            totals = res.get("totals") or {}
            rows.append({
                "portco_id": portco,
                "audience": audience,
                "memo_path": str(memo),
                "source_path": str(source),
                "citation_accuracy": scores.get("citation_accuracy", 0.0),
                "hallucination_rate": scores.get("hallucination_rate", 0.0),
                "coverage": scores.get("coverage", 0.0),
                "consistency": scores.get("consistency", 0.0),
                "figures_total": totals.get("figures_total", 0),
                "figures_cited": totals.get("figures_cited", 0),
                "entities_total": totals.get("entities_total", 0),
                "entities_grounded": totals.get("entities_grounded", 0),
                "opps_in_source": totals.get("opps_in_source", 0),
                "opps_addressed": totals.get("opps_addressed", 0),
            })
            print(
                f"[eval] {portco:<22} {audience:<8} "
                f"cite={scores.get('citation_accuracy', 0):.2f} "
                f"halluc={scores.get('hallucination_rate', 0):.2f} "
                f"cov={scores.get('coverage', 0):.2f} "
                f"consist={scores.get('consistency', 0):.2f}"
            )
        except Exception as exc:
            print(f"[eval] {portco} {audience} skipped: {exc}")
            rows.append({
                "portco_id": portco,
                "audience": audience,
                "error": str(exc),
            })

    # Aggregate
    valid = [r for r in rows if "error" not in r]
    n = len(valid)
    means = {
        "citation_accuracy": sum(r["citation_accuracy"] for r in valid) / n if n else 0,
        "hallucination_rate": sum(r["hallucination_rate"] for r in valid) / n if n else 0,
        "coverage": sum(r["coverage"] for r in valid) / n if n else 0,
        "consistency": sum(r["consistency"] for r in valid) / n if n else 0,
    }
    summary = {
        "as_of": date.today().isoformat(),
        "n_memos_evaluated": len(rows),
        "n_valid": n,
        "n_errored": len(rows) - n,
        "corpus_means": means,
        "rows": rows,
    }

    json_path = OUT / "eval_corpus_summary.json"
    json_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"[eval] JSON  → {json_path}")

    # Render an HTML summary in editorial-letterpress style
    rows_html = "\n".join(
        f'<tr class="{("err" if "error" in r else "")}">'
        f'<td>{r.get("portco_id","")}</td>'
        f'<td>{r.get("audience","")}</td>'
        f'<td class="num">{r.get("citation_accuracy",0):.2f}</td>'
        f'<td class="num">{r.get("hallucination_rate",0):.2f}</td>'
        f'<td class="num">{r.get("coverage",0):.2f}</td>'
        f'<td class="num">{r.get("consistency",0):.2f}</td>'
        f'<td class="num">{r.get("figures_cited",0)}/{r.get("figures_total",0)}</td>'
        f'<td class="num">{r.get("opps_addressed",0)}/{r.get("opps_in_source",0)}</td>'
        '</tr>'
        for r in rows
    )

    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8" />
<title>Eval corpus summary — {len(rows)} memos</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;500&family=EB+Garamond:wght@400;500&family=Newsreader:ital,wght@1,300&family=JetBrains+Mono:wght@400&display=swap" rel="stylesheet" />
<style>
  :root {{
    --paper: #f4ecd5; --page: #fbf6e2; --ink: #1a140d; --ink-dim: #5a4a35;
    --rule: #c2ad84; --rule-soft: #dfd2af; --accent: #6b1414;
  }}
  * {{ box-sizing: border-box; }} html, body {{ margin: 0; padding: 0; }}
  body {{ background: var(--paper); color: var(--ink); padding: 64px 20px; font-family: 'EB Garamond', serif; font-size: 16px; line-height: 1.6; -webkit-font-smoothing: antialiased; }}
  .sheet {{ max-width: 880px; margin: 0 auto; background: var(--page); padding: 64px 60px; box-shadow: 0 30px 60px -30px rgba(60,40,15,0.18); border: 1px solid rgba(194,173,132,0.45); }}
  .eyebrow {{ font-variant: small-caps; letter-spacing: 0.18em; font-size: 12px; color: var(--accent); font-weight: 600; }}
  h1 {{ font-family: 'Cormorant Garamond', serif; font-weight: 400; font-size: 42px; line-height: 1.08; margin: 12px 0 12px; }}
  h1 em {{ font-style: italic; color: var(--accent); }}
  .lede {{ font-style: italic; color: var(--ink-dim); margin: 0 0 32px; }}
  .stats-strip {{ display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule); padding: 18px 0; margin: 24px 0 36px; }}
  .stat {{ text-align: center; padding: 0 14px; border-right: 1px solid var(--rule-soft); }}
  .stat:last-child {{ border-right: none; }}
  .stat-label {{ font-variant: small-caps; letter-spacing: 0.16em; font-size: 11px; color: var(--ink-dim); margin-bottom: 6px; }}
  .stat-num {{ font-family: 'Cormorant Garamond', serif; font-weight: 500; font-size: 28px; color: var(--ink); }}
  table {{ width: 100%; border-collapse: collapse; margin: 28px 0; font-size: 14px; }}
  th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--rule-soft); text-align: left; }}
  th {{ font-variant: small-caps; letter-spacing: 0.14em; font-size: 11px; color: var(--ink-dim); border-bottom: 1px solid var(--rule); }}
  td.num {{ font-family: 'JetBrains Mono', monospace; font-size: 13px; text-align: right; }}
  tr.err td {{ color: #b35a1f; }}
  .colophon {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid var(--rule); font-family: 'Newsreader', serif; font-style: italic; font-size: 12px; color: var(--ink-dim); text-align: center; }}
  .colophon code {{ font-family: 'JetBrains Mono', monospace; font-style: normal; font-size: 11px; background: rgba(194,173,132,0.18); padding: 1px 6px; border-radius: 2px; }}
</style></head><body>
<article class="sheet">
  <div class="eyebrow">— Eval corpus summary —</div>
  <h1>{len(rows)} memos scored,<br /><em>against {n} OpportunityMap sources.</em></h1>
  <p class="lede">Corpus-level eval of every explainer memo against its DX OpportunityMap source. Citation accuracy is the rubric's primary score; hallucination rate captures whitelisted prose drift; coverage measures how many opportunities each memo addresses; consistency cross-checks board vs. operator audiences for the same source.</p>

  <div class="stats-strip">
    <div class="stat"><div class="stat-label">Citation</div><div class="stat-num">{means["citation_accuracy"]:.0%}</div></div>
    <div class="stat"><div class="stat-label">Hallucination</div><div class="stat-num">{means["hallucination_rate"]:.0%}</div></div>
    <div class="stat"><div class="stat-label">Coverage</div><div class="stat-num">{means["coverage"]:.0%}</div></div>
    <div class="stat"><div class="stat-label">Consistency</div><div class="stat-num">{means["consistency"]:.0%}</div></div>
  </div>

  <table>
    <thead><tr>
      <th>Portco</th><th>Audience</th><th>Citation</th><th>Hallucination</th>
      <th>Coverage</th><th>Consistency</th><th>Figures</th><th>Opps</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>

  <p class="colophon">
    Generated by <code>scripts/run_eval_corpus.py</code> wrapping <code>eval_pe_output</code>.
    Every score traces back to a source OpportunityMap field — no LLM at runtime,
    no fabricated metrics. Generated {summary["as_of"]}.
  </p>
</article>
</body></html>
"""
    html_path = OUT / "eval_corpus_summary.html"
    html_path.write_text(html)
    print(f"[eval] HTML  → {html_path}")
    print(f"[eval] corpus means: {means}")


if __name__ == "__main__":
    main()
