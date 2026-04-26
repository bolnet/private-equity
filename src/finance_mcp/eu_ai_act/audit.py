"""
ai_act_audit — Generate a Regulation (EU) 2024/1689 compliance documentation
pack for a portco AI system.

Architectural shape mirrors `explainer/explain.py`: pure deterministic
Python on the inputs (no LLM call inside the tool), templated prose on
the outputs, an HTML render at the end, and a JSON sidecar for
downstream consumption.

The hook is the **2 August 2026** high-risk obligations effective date
under Article 113 of the regulation. From that date, providers of
high-risk AI systems (those listed in Annex III, or product safety
components under Annex I) must have produced — and keep current — the
documentation skeleton this tool emits.

Inputs are minimal by design: the user names the portco, describes the
system in prose, and picks an Annex III use-case key. The tool
deterministically:

  1. Classifies the system (high-risk / limited-risk / minimal-risk)
     with citation to the regulation.
  2. Selects the relevant article requirements (Article 6, plus 9–15
     for high-risk; Article 50 for limited-risk).
  3. Renders a per-article documentation skeleton populated with the
     portco context.
  4. Writes a printable HTML compliance pack and a JSON sidecar.

No external network calls. The Annex III list is hardcoded in
`annex_iii.py` and verifiable against EUR-Lex.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Literal

from fastmcp.exceptions import ToolError

from finance_mcp.eu_ai_act.annex_iii import (
    ANNEX_III_BY_KEY,
    NON_HIGH_RISK_HINTS,
    REGULATION_CITATION,
    REGULATION_URL,
    AnnexIIICategory,
    list_keys,
)
from finance_mcp.eu_ai_act.articles import (
    ARTICLE_6,
    ARTICLE_50,
    HIGH_RISK_ARTICLES,
    HIGH_RISK_DEADLINE,
    ArticleRequirement,
)


RiskTier = Literal["high-risk", "limited-risk", "minimal-risk"]

# Output directory. Mirrors the convention used by other tools in the repo.
DEFAULT_OUTPUT_DIR: Path = Path("finance_output")

# Keys deliberately NOT in Annex III but commonly proposed for audit.
# Surface a soft-classification verdict + Article 50 reference.
KNOWN_NON_HIGH_RISK_KEYS: frozenset[str] = frozenset(NON_HIGH_RISK_HINTS.keys())

# A liberal slug regex for portco_id. Mirrors the explainer convention.
_SLUG_RE = re.compile(r"[^A-Za-z0-9_\-]+")


def _slug(s: str) -> str:
    """Filename-safe slug; collapses non-[A-Za-z0-9_-] runs to '_'."""
    return _SLUG_RE.sub("_", s).strip("_") or "portco"


def _classify(use_case_category: str) -> tuple[RiskTier, str, AnnexIIICategory | None]:
    """Deterministic Article 6 classification.

    Returns:
        (risk_tier, citation_or_reasoning, matched_category_or_None)
    """
    key = use_case_category.strip().lower()
    if not key:
        raise ToolError("use_case_category must be a non-empty string.")

    matched = ANNEX_III_BY_KEY.get(key)
    if matched is not None:
        citation = (
            f"High-risk per {matched.annex_ref} of {REGULATION_CITATION} "
            f"(area: {matched.area})."
        )
        return ("high-risk", citation, matched)

    hint = NON_HIGH_RISK_HINTS.get(key)
    if hint is not None:
        citation = (
            f"Not enumerated in Annex III. {hint} Article 50 transparency "
            f"obligations may still apply."
        )
        return ("limited-risk", citation, None)

    # Unknown key — fail fast with the valid set surfaced to the caller.
    valid = ", ".join(list_keys() + tuple(KNOWN_NON_HIGH_RISK_KEYS))
    raise ToolError(
        f"Unknown use_case_category '{use_case_category}'. Valid keys: {valid}."
    )


def _deadline_clause(tier: RiskTier) -> str:
    if tier == "high-risk":
        return (
            f"The high-risk obligations of Articles 9–15 enter into "
            f"force on {HIGH_RISK_DEADLINE.isoformat()} (Article 113 of "
            f"the regulation). Documentation evidencing each obligation "
            f"must be in place by that date."
        )
    if tier == "limited-risk":
        return (
            f"Article 50 transparency obligations enter into force on "
            f"{HIGH_RISK_DEADLINE.isoformat()}. Disclosure to natural "
            f"persons interacting with the system, and provenance "
            f"signalling for any synthetic content, are required from "
            f"that date."
        )
    return (
        "No binding obligations under the AI Act apply at this time. "
        "Voluntary alignment with Article 9 risk management practice "
        "is recommended for governance maturity."
    )


def _article_context(
    article: ArticleRequirement,
    portco_id: str,
    system_description: str,
    matched: AnnexIIICategory | None,
) -> str:
    """Per-article populated context paragraph — deterministic templated
    prose drawn strictly from the user-supplied inputs."""
    cohort_clause = (
        f" — applied to the Annex III area '{matched.area}' "
        f"(see {matched.annex_ref})"
        if matched is not None
        else ""
    )
    return (
        f"For **{portco_id}**, this requirement applies to the system "
        f"described as: _{system_description.strip()}_{cohort_clause}. "
        f"The deliverables listed below constitute the minimum skeleton "
        f"the operator must populate and maintain on file."
    )


def _articles_for(tier: RiskTier) -> tuple[ArticleRequirement, ...]:
    if tier == "high-risk":
        return HIGH_RISK_ARTICLES
    if tier == "limited-risk":
        return (ARTICLE_6, ARTICLE_50)
    # minimal-risk — keep Article 6 as the classification anchor.
    return (ARTICLE_6,)


_ARTICLE_BLOCK = """\
<section class="article-section">

  <header class="article-head">
    <div class="article-marker">{article_label}</div>
    <div class="article-titles">
      <div class="article-eyebrow">{eyebrow}</div>
      <h2 class="article-title">{title}</h2>
    </div>
  </header>

  <div class="article-body">

    <h3>Regulation summary</h3>
    <p>{summary}</p>

    <h3>Application to this system</h3>
    <p>{context}</p>

    <h3>Deliverables to maintain on file</h3>
    <ol class="deliverables">
      {deliverables_html}
    </ol>

    <div class="article-ledger">
      <div class="cell">
        <div class="cell-label">Article</div>
        <div class="cell-val">{article_label}</div>
      </div>
      <div class="cell">
        <div class="cell-label">Effective</div>
        <div class="cell-val">{effective_date}</div>
      </div>
      <div class="cell">
        <div class="cell-label">Deliverables</div>
        <div class="cell-val">{n_deliverables}</div>
      </div>
    </div>

  </div>

</section>
"""


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>EU AI Act compliance pack — {portco_id}</title>
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
    --max:      720px;
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
    font-size: 18px;
    line-height: 1.62;
    font-feature-settings: "liga", "dlig", "onum", "kern";
    text-rendering: optimizeLegibility;
    -webkit-font-smoothing: antialiased;
    padding: 72px 20px 96px;
  }}
  body::before {{
    content: '';
    position: fixed; inset: 0;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0.42  0 0 0 0 0.34  0 0 0 0 0.18  0 0 0 0.07 0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>");
    opacity: 0.35; pointer-events: none; z-index: 0; mix-blend-mode: multiply;
  }}
  .sheet {{
    max-width: var(--max);
    margin: 0 auto;
    background: var(--page);
    position: relative; z-index: 1;
    padding: 88px 76px 72px;
    box-shadow:
      0 1px 0 var(--rule-soft),
      0 30px 60px -30px rgba(60, 40, 15, 0.18),
      0 8px 18px -6px rgba(60, 40, 15, 0.08);
    border: 1px solid rgba(194, 173, 132, 0.45);
  }}
  .letterhead {{
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: 24px; margin-bottom: 56px;
  }}
  .wordmark {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 500; font-style: italic;
    font-size: 22px; letter-spacing: 0.01em;
    color: var(--ink); display: flex; align-items: center; gap: 14px;
  }}
  .wordmark .seal {{
    width: 36px; height: 36px; border-radius: 50%;
    border: 1px solid var(--accent); color: var(--accent);
    display: inline-flex; align-items: center; justify-content: center;
    font-family: 'Cormorant Garamond', serif;
    font-style: italic; font-size: 18px; font-weight: 500;
    background: rgba(107, 20, 20, 0.04);
  }}
  .letterhead-meta {{
    text-align: right;
    font-family: 'Newsreader', 'EB Garamond', serif;
    font-style: italic; font-weight: 300;
    font-size: 13px; line-height: 1.55;
    color: var(--ink-faint);
    letter-spacing: 0.02em;
  }}
  .letterhead-meta strong {{
    display: block; color: var(--ink-dim); font-style: normal; font-weight: 500;
    font-variant: small-caps; letter-spacing: 0.14em; font-size: 11px;
    margin-bottom: 2px;
  }}
  .title-block {{ margin-bottom: 36px; }}
  .eyebrow {{
    font-variant: small-caps; letter-spacing: 0.18em; font-size: 12px;
    color: var(--accent); font-weight: 600; margin-bottom: 14px;
    display: inline-block;
  }}
  .eyebrow::before {{ content: '— '; color: var(--rule); }}
  .eyebrow::after  {{ content: ' —'; color: var(--rule); }}
  h1 {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 400;
    font-size: 50px; line-height: 1.06;
    letter-spacing: -0.005em;
    margin: 0 0 18px; color: var(--ink);
  }}
  h1 em {{
    font-style: italic; color: var(--accent);
    font-weight: 500;
  }}
  .lede {{
    font-family: 'EB Garamond', serif;
    font-style: italic; color: var(--ink-dim);
    font-size: 20px; line-height: 1.55;
    margin: 0; max-width: 56ch;
  }}

  /* Verdict callout — the centrepiece of this report */
  .verdict {{
    margin: 36px 0;
    border: 1px solid var(--rule);
    background: rgba(255, 247, 215, 0.55);
    padding: 26px 28px;
    position: relative;
  }}
  .verdict::before {{
    content: ''; position: absolute; left: 0; top: -3px; width: 64px; height: 5px;
    border-top: 2px solid var(--accent);
    border-bottom: 1px solid var(--accent);
  }}
  .verdict-label {{
    font-variant: small-caps; letter-spacing: 0.18em; font-size: 12px;
    font-weight: 600; color: var(--accent); margin-bottom: 8px;
  }}
  .verdict-tier {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 500; font-size: 38px; line-height: 1.05;
    color: var(--ink); margin: 0 0 10px;
  }}
  .verdict-tier em {{ font-style: italic; color: var(--accent); font-weight: 500; }}
  .verdict-citation {{
    font-family: 'Newsreader', 'EB Garamond', serif;
    font-style: italic; font-size: 15px; color: var(--ink-dim);
    margin: 0;
  }}

  .deadline-strip {{
    display: grid; grid-template-columns: repeat(3, 1fr);
    gap: 0; margin: 32px 0 56px;
    border-top: 1px solid var(--rule);
    border-bottom: 1px solid var(--rule);
    padding: 18px 0;
  }}
  .deadline-strip .stat {{
    text-align: center; padding: 0 16px;
    border-right: 1px solid var(--rule-soft);
  }}
  .deadline-strip .stat:last-child {{ border-right: none; }}
  .deadline-strip .stat-label {{
    font-variant: small-caps; letter-spacing: 0.16em; font-size: 11px;
    color: var(--ink-faint); font-weight: 500; margin-bottom: 6px;
  }}
  .deadline-strip .stat-num {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 500; font-size: 22px; line-height: 1; color: var(--ink);
    font-feature-settings: "lnum";
  }}
  .deadline-strip .stat-sub {{
    font-family: 'Newsreader', serif; font-style: italic; font-weight: 300;
    font-size: 11px; color: var(--ink-faint); margin-top: 4px;
  }}

  .ornament {{
    text-align: center; color: var(--rule);
    font-family: 'Cormorant Garamond', serif;
    font-size: 22px; letter-spacing: 1.2em;
    margin: 44px 0;
    padding-left: 1.2em;
  }}
  .ornament::before {{ content: '✦  ✦  ✦'; }}

  .article-section {{
    margin: 60px 0;
  }}
  .article-head {{
    display: flex; align-items: baseline; gap: 22px;
    padding-bottom: 14px; margin-bottom: 22px;
    border-bottom: 1px solid var(--rule);
  }}
  .article-marker {{
    font-family: 'Cormorant Garamond', serif;
    font-style: italic; font-weight: 400;
    font-size: 26px; color: var(--accent);
    line-height: 1; min-width: 110px;
  }}
  .article-titles {{ flex: 1; min-width: 0; }}
  .article-eyebrow {{
    font-variant: small-caps; letter-spacing: 0.16em; font-size: 11px;
    font-weight: 600; color: var(--ink-faint);
    margin-bottom: 4px;
  }}
  .article-title {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 500; font-size: 26px; line-height: 1.2;
    color: var(--ink); margin: 0;
  }}
  .article-body h3 {{
    font-family: 'EB Garamond', serif;
    font-variant: small-caps; letter-spacing: 0.14em;
    font-weight: 600; font-size: 14px; color: var(--accent);
    margin: 24px 0 8px;
  }}
  .article-body h3::before {{
    content: ''; display: inline-block; width: 18px; height: 1px;
    background: var(--accent); vertical-align: middle;
    margin-right: 10px; transform: translateY(-3px);
  }}
  .article-body p {{ margin: 0 0 14px; text-align: justify; hyphens: auto; }}
  .article-body p strong {{ font-weight: 600; color: var(--ink); }}
  .deliverables {{
    margin: 4px 0 18px 22px; padding: 0;
  }}
  .deliverables li {{
    margin: 6px 0; padding-left: 6px;
  }}
  .article-ledger {{
    display: grid; grid-template-columns: repeat(3, 1fr);
    gap: 0; margin: 22px 0 0;
    padding: 14px 0;
    border-top: 1px dashed var(--rule);
    border-bottom: 1px dashed var(--rule);
    background: rgba(255, 247, 215, 0.5);
  }}
  .article-ledger .cell {{
    text-align: center; padding: 0 10px;
    border-right: 1px dotted var(--rule-soft);
  }}
  .article-ledger .cell:last-child {{ border-right: none; }}
  .article-ledger .cell-label {{
    font-variant: small-caps; letter-spacing: 0.16em; font-size: 10px;
    color: var(--ink-faint); font-weight: 500; margin-bottom: 4px;
  }}
  .article-ledger .cell-val {{
    font-family: 'Cormorant Garamond', serif;
    font-weight: 500; font-size: 16px; line-height: 1.1; color: var(--ink);
    font-feature-settings: "lnum";
  }}

  .colophon {{
    margin-top: 72px; padding-top: 24px;
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
    font-size: 16px; color: var(--ink-dim);
    margin-bottom: 12px;
  }}
  .colophon .signature::before {{
    content: ''; display: block; width: 120px; height: 1px;
    background: var(--ink-faint); opacity: 0.5;
    margin: 0 auto 10px;
  }}
  .colophon a {{ color: var(--accent); text-decoration: none; }}
  .colophon a:hover {{ text-decoration: underline; }}

  @media print {{
    html, body {{ background: white; }}
    body::before {{ display: none; }}
    .sheet {{ box-shadow: none; border: none; padding: 24mm; max-width: none; }}
  }}

  @media (max-width: 720px) {{
    body {{ padding: 32px 8px 64px; font-size: 17px; }}
    .sheet {{ padding: 48px 28px 56px; }}
    h1 {{ font-size: 36px; }}
    .deadline-strip, .article-ledger {{ grid-template-columns: repeat(2, 1fr); gap: 16px; padding: 14px 0; }}
    .deadline-strip .stat, .article-ledger .cell {{ border-right: none; border-bottom: 1px dotted var(--rule-soft); padding-bottom: 12px; }}
    .article-title {{ font-size: 22px; }}
    .article-marker {{ font-size: 20px; min-width: 80px; }}
    .verdict-tier {{ font-size: 28px; }}
    .letterhead {{ flex-direction: column; gap: 8px; }}
    .letterhead-meta {{ text-align: left; }}
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
      <strong>EU AI Act compliance pack</strong>
      In re: {portco_id}<br />
      {as_of} &middot; Reg. (EU) 2024/1689
    </div>
  </header>

  <section class="title-block">
    <div class="eyebrow">A documentation skeleton for {portco_id}</div>
    <h1>{n_articles} articles addressed,<br /><em>{tier_human} verdict.</em></h1>
    <p class="lede">{lede}</p>
  </section>

  <section class="verdict">
    <div class="verdict-label">Article 6 classification verdict</div>
    <h2 class="verdict-tier"><em>{tier_human}</em></h2>
    <p class="verdict-citation">{verdict_citation}</p>
  </section>

  <div class="deadline-strip">
    <div class="stat">
      <div class="stat-label">Deadline</div>
      <div class="stat-num">{deadline_iso}</div>
      <div class="stat-sub">Article 113 effective date</div>
    </div>
    <div class="stat">
      <div class="stat-label">Use case</div>
      <div class="stat-num">{use_case_label}</div>
      <div class="stat-sub">{annex_ref}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Articles</div>
      <div class="stat-num">{n_articles}</div>
      <div class="stat-sub">covered in this pack</div>
    </div>
  </div>

  <div class="ornament"></div>

  <section class="title-block">
    <div class="eyebrow">Deadline reminder</div>
    <p>{deadline_clause}</p>
  </section>

  {article_blocks}

  <footer class="colophon">
    <div class="signature">Composed at the AI-Act audit layer.</div>
    Generated by <code>ai_act_audit</code>. Annex III categories and Article
    9–15 schemas are frozen from the public regulation text and verifiable
    against EUR-Lex: <a href="{regulation_url}">{regulation_url}</a>.<br />
    Pack output: <code>{output_filename}</code> &middot; {as_of}.
  </footer>

</article>

</body>
</html>
"""


def _render_deliverables_html(deliverables: tuple[str, ...]) -> str:
    return "\n      ".join(f"<li>{d}</li>" for d in deliverables)


def _build_article_block(
    article: ArticleRequirement,
    portco_id: str,
    system_description: str,
    matched: AnnexIIICategory | None,
) -> str:
    return _ARTICLE_BLOCK.format(
        article_label=article.article,
        eyebrow=f"{article.article} of Reg. (EU) 2024/1689",
        title=article.title,
        summary=article.summary,
        context=_article_context(article, portco_id, system_description, matched),
        deliverables_html=_render_deliverables_html(article.deliverables),
        effective_date=article.effective_date.isoformat(),
        n_deliverables=len(article.deliverables),
    )


def _human_tier(tier: RiskTier) -> str:
    return {
        "high-risk": "High-risk",
        "limited-risk": "Limited-risk",
        "minimal-risk": "Minimal-risk",
    }[tier]


def ai_act_audit(
    portco_id: str,
    ai_system_description: str,
    use_case_category: str,
    output_filename: str | None = None,
) -> dict:
    """Generate an EU AI Act (Reg. 2024/1689) compliance documentation pack
    for a portco AI system.

    Args:
        portco_id:               Portfolio company identifier (slugged for filenames).
        ai_system_description:   Free-text description of the AI system in scope.
        use_case_category:       One of the snake_case keys defined in
                                 ``annex_iii.py`` (e.g. ``"credit_decisioning"``,
                                 ``"employment"``, ``"law_enforcement"``), or
                                 a known non-high-risk hint key (e.g.
                                 ``"marketing_personalization"``).
        output_filename:         Optional HTML basename. Defaults to
                                 ``ai_act_audit_<portco>.html``.

    Returns:
        dict with ``report_path``, ``json_path``, ``high_risk_classification``,
        ``articles_addressed``, ``deadline``.

    Raises:
        ToolError on invalid inputs (empty fields, unknown use-case key).
    """
    portco_id = (portco_id or "").strip()
    if not portco_id:
        raise ToolError("portco_id must be a non-empty string.")

    description = (ai_system_description or "").strip()
    if not description:
        raise ToolError("ai_system_description must be a non-empty string.")

    if len(description) > 4000:
        raise ToolError(
            "ai_system_description is too long (max 4000 chars). "
            "Trim to a one-paragraph description of the AI system."
        )

    tier, citation, matched = _classify(use_case_category)
    articles = _articles_for(tier)

    # Per-article context blocks (HTML) and structured records (JSON).
    article_blocks_html: list[str] = []
    structured_articles: list[dict] = []
    for article in articles:
        article_blocks_html.append(
            _build_article_block(article, portco_id, description, matched)
        )
        structured_articles.append(
            {
                "article": article.article,
                "title": article.title,
                "summary": article.summary,
                "deliverables": list(article.deliverables),
                "effective_date": article.effective_date.isoformat(),
                "context": _article_context(
                    article, portco_id, description, matched
                ),
            }
        )

    use_case_label = (
        matched.area if matched is not None else use_case_category.strip().lower()
    )
    annex_ref = matched.annex_ref if matched is not None else "Article 50 (transparency)"

    lede = (
        "A per-article documentation skeleton mapping the AI Act's "
        "high-risk obligations to a specific portco system. Every "
        "deliverable below traces to a public article of "
        "Reg. (EU) 2024/1689 — no obligation invented in prose."
    )

    html = _HTML_TEMPLATE.format(
        portco_id=portco_id,
        as_of=date.today().isoformat(),
        tier_human=_human_tier(tier),
        verdict_citation=citation,
        deadline_iso=HIGH_RISK_DEADLINE.isoformat(),
        deadline_clause=_deadline_clause(tier),
        use_case_label=use_case_label,
        annex_ref=annex_ref,
        n_articles=len(articles),
        article_blocks="\n".join(article_blocks_html),
        regulation_url=REGULATION_URL,
        output_filename=output_filename or f"ai_act_audit_{_slug(portco_id)}.html",
        lede=lede,
    )

    out_dir = DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_name = output_filename or f"ai_act_audit_{_slug(portco_id)}.html"
    out_path = out_dir / out_name
    out_path.write_text(html, encoding="utf-8")

    json_out_path = out_path.with_suffix(".json")
    json_payload = {
        "portco_id": portco_id,
        "as_of": date.today().isoformat(),
        "ai_system_description": description,
        "use_case_category": use_case_category.strip().lower(),
        "high_risk_classification": tier,
        "classification_citation": citation,
        "matched_annex_iii_category": (
            asdict(matched) if matched is not None else None
        ),
        "deadline": HIGH_RISK_DEADLINE.isoformat(),
        "articles_addressed": [a["article"] for a in structured_articles],
        "articles": structured_articles,
        "regulation": {
            "citation": REGULATION_CITATION,
            "url": REGULATION_URL,
        },
    }
    json_out_path.write_text(
        json.dumps(json_payload, indent=2, default=str),
        encoding="utf-8",
    )

    return {
        "report_path": str(out_path.resolve()),
        "json_path": str(json_out_path.resolve()),
        "high_risk_classification": tier,
        "articles_addressed": [a.article for a in articles],
        "deadline": HIGH_RISK_DEADLINE.isoformat(),
    }
