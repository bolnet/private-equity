"""
Render per-beat still frames for the explainer videos.

For each beat in .local/demo/voiceover-scripts.md, this script:
  1. Loads the right URL (catalogue, generated report, etc.)
  2. Scrolls to the target element
  3. Applies a graphical highlight overlay matching the editorial PE-gazette
     palette (oxblood spotlight · paper-glow · Cormorant italic margin labels)
  4. Renders a high-resolution PNG
  5. Saves to /tmp/pe-demo/frames/<tool>/<beat>.png

The output is a deck of stills the user can drop into a video editor and
animate with cross-fades, ken-burns zoom, and matching voiceover.

Usage:
    python scripts/render_beat_frames.py --tool 01-dx
    python scripts/render_beat_frames.py --tool all
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import Page, sync_playwright


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------
OUT_ROOT = Path("/tmp/pe-demo/frames")
OUT_ROOT.mkdir(parents=True, exist_ok=True)

VIEWPORT = {"width": 1920, "height": 1080}
SERVER = "http://localhost:8765"


# ----------------------------------------------------------------------------
# CSS / JS injected into every page for the highlight grammar.
#
# Vocabulary (matches the editorial gazette palette):
#   .__hl-spotlight  — oxblood ring + paper glow; rest of page dimmed
#   .__hl-bloom      — figure scales 1.04x with oxblood underline pseudo
#   .__hl-callout    — small italic Cormorant note in margin with dotted line
#   .__hl-curtain    — full-page paper veil (40% opacity) over everything
#                      EXCEPT spotlit element (uses outline + box-shadow)
# ----------------------------------------------------------------------------
_OVERLAY_CSS = r"""
    /* Hide any caption overlay left over from the recorder. */
    #__demo_caption_root { display: none !important; }

    /* Curtain — soft paper veil over the page. */
    .__hl-curtain {
        position: fixed; inset: 0; z-index: 9000;
        background:
          radial-gradient(ellipse 1100px 700px at 50% 50%,
            transparent 0%, rgba(60,40,15,0.42) 80%);
        pointer-events: none;
    }

    /* Spotlit element — sits above the curtain and gets a luminous outline. */
    .__hl-spotlight {
        position: relative; z-index: 9100 !important;
        outline: 2px solid #6b1414 !important;
        outline-offset: 6px;
        background-color: rgba(251, 246, 226, 0.98) !important;
        box-shadow:
            0 0 0 8px rgba(107, 20, 20, 0.10),
            0 0 0 16px rgba(255, 248, 220, 0.45),
            0 24px 64px -12px rgba(60, 40, 15, 0.45) !important;
        animation: none;
    }

    /* Bloom — number/figure scales up with an oxblood underline. */
    .__hl-bloom {
        position: relative; z-index: 9100 !important;
        transform: scale(1.04);
        transform-origin: center;
        text-shadow: 0 0 24px rgba(255, 248, 220, 0.6);
    }
    .__hl-bloom::after {
        content: '';
        position: absolute; left: 0; right: 0; bottom: -8px;
        height: 3px;
        background: #6b1414;
        box-shadow: 0 1px 0 rgba(107,20,20,0.30);
    }

    /* Callout — italic Cormorant margin note with a dotted leader. */
    .__hl-callout-root {
        position: fixed; z-index: 9200;
        font-family: 'Cormorant Garamond', 'EB Garamond', Georgia, serif;
        font-style: italic; font-weight: 500;
        color: #6b1414;
        font-size: 22px; line-height: 1.3;
        letter-spacing: 0.005em;
        padding: 12px 18px;
        background: rgba(251, 246, 226, 0.96);
        border-left: 2px solid #6b1414;
        max-width: 280px;
        box-shadow: 0 16px 36px -8px rgba(60, 40, 15, 0.30);
    }
    .__hl-callout-root::before {
        content: '';
        position: absolute; left: -28px; top: 50%;
        width: 26px; height: 2px;
        border-top: 2px dotted #6b1414;
    }

    /* Caption overlay — beat number + voice-script line, bottom-anchored. */
    .__hl-caption {
        position: fixed; left: 50%; bottom: 36px;
        transform: translateX(-50%);
        z-index: 9300;
        font-family: 'Cormorant Garamond', 'EB Garamond', Georgia, serif;
        background: rgba(26, 20, 13, 0.94);
        color: #f4ecd5;
        padding: 14px 22px 16px;
        max-width: 920px; width: calc(100% - 80px);
        border: 1px solid rgba(194, 173, 132, 0.30);
        box-shadow: 0 24px 64px -16px rgba(0, 0, 0, 0.50);
    }
    .__hl-caption .__hl-caption-kicker {
        font-family: 'Cormorant Garamond', serif;
        font-style: italic; font-weight: 500;
        font-size: 13px; letter-spacing: 0.30em; text-transform: uppercase;
        color: #c2ad84;
        margin-bottom: 4px;
    }
    .__hl-caption .__hl-caption-text {
        font-family: 'EB Garamond', 'Iowan Old Style', Georgia, serif;
        font-style: italic; font-weight: 400;
        font-size: 22px; line-height: 1.4; color: #f4ecd5;
    }
    .__hl-caption .__hl-caption-text strong {
        color: #ffffff; font-style: normal; font-weight: 500;
    }
"""

_OVERLAY_JS = r"""
(css) => {
    if (window.__overlay_installed) return;
    window.__overlay_installed = true;
    const style = document.createElement('style');
    style.id = '__overlay_css';
    style.textContent = css;
    document.head.appendChild(style);

    window.__hl_clear = function() {
        document.querySelectorAll('.__hl-curtain, .__hl-callout-root, .__hl-caption')
            .forEach(el => el.remove());
        document.querySelectorAll('.__hl-spotlight, .__hl-bloom').forEach(el => {
            el.classList.remove('__hl-spotlight', '__hl-bloom');
            el.style.zIndex = ''; el.style.outlineOffset = '';
        });
    };

    window.__hl_curtain = function() {
        if (document.querySelector('.__hl-curtain')) return;
        const c = document.createElement('div');
        c.className = '__hl-curtain';
        document.body.appendChild(c);
    };

    window.__hl_spotlight = function(selector, withCurtain) {
        const el = document.querySelector(selector);
        if (!el) return false;
        el.classList.add('__hl-spotlight');
        if (withCurtain) window.__hl_curtain();
        el.scrollIntoView({block: 'center', inline: 'nearest'});
        return true;
    };

    window.__hl_bloom = function(selector) {
        const el = document.querySelector(selector);
        if (!el) return false;
        el.classList.add('__hl-bloom');
        el.scrollIntoView({block: 'center', inline: 'nearest'});
        return true;
    };

    window.__hl_callout = function(targetSelector, text, side) {
        const el = document.querySelector(targetSelector);
        if (!el) return false;
        const r = el.getBoundingClientRect();
        const note = document.createElement('div');
        note.className = '__hl-callout-root';
        note.textContent = text;
        const left = side === 'left' ? Math.max(20, r.left - 320) : Math.min(window.innerWidth - 320, r.right + 28);
        note.style.left = left + 'px';
        note.style.top = (Math.max(40, r.top + r.height / 2 - 30)) + 'px';
        document.body.appendChild(note);
        return true;
    };

    window.__hl_caption = function(kicker, text) {
        document.querySelectorAll('.__hl-caption').forEach(e => e.remove());
        const root = document.createElement('div');
        root.className = '__hl-caption';
        const k = document.createElement('div');
        k.className = '__hl-caption-kicker';
        k.textContent = kicker;
        const t = document.createElement('div');
        t.className = '__hl-caption-text';
        t.innerHTML = text;
        root.appendChild(k); root.appendChild(t);
        document.body.appendChild(root);
    };
}
"""


def _install_overlay(page: Page) -> None:
    page.evaluate(_OVERLAY_JS, _OVERLAY_CSS)


def _clear(page: Page) -> None:
    page.evaluate("window.__hl_clear && window.__hl_clear()")


def _spotlight(page: Page, selector: str, curtain: bool = True) -> None:
    page.evaluate(
        "(args) => window.__hl_spotlight(args[0], args[1])",
        [selector, curtain],
    )


def _bloom(page: Page, selector: str) -> None:
    page.evaluate("(s) => window.__hl_bloom(s)", selector)


def _callout(page: Page, selector: str, text: str, side: str = "right") -> None:
    page.evaluate(
        "(args) => window.__hl_callout(args[0], args[1], args[2])",
        [selector, text, side],
    )


def _caption(page: Page, kicker: str, text: str) -> None:
    page.evaluate(
        "(args) => window.__hl_caption(args[0], args[1])",
        [kicker, text],
    )


def _scroll(page: Page, selector: str | None) -> None:
    if not selector:
        page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
        return
    page.evaluate(
        f"(s) => {{ const el = document.querySelector(s); if (el) el.scrollIntoView({{block:'center', behavior:'instant'}}); }}",
        selector,
    )


# ----------------------------------------------------------------------------
# Beat declaration
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Beat:
    n: str                 # zero-padded beat number ("01" .. "14")
    url: str               # absolute URL or path under SERVER
    kicker: str            # small caps caption label
    line: str              # main caption text (matches voiceover script)
    scroll_to: str | None = None      # selector to scroll into view first
    actions: list[Callable[[Page], None]] = field(default_factory=list)


def _abs(path: str) -> str:
    return path if path.startswith("http") else f"{SERVER}{path}"


# ----------------------------------------------------------------------------
# Tool I — Decision Diagnostic (DX) — 14 beats
# ----------------------------------------------------------------------------

def _tool_dx_beats() -> list[Beat]:
    return [
        Beat(
            n="01",
            url=_abs("/app/tools.html"),
            kicker="I · Decision Diagnostic · Beat 1 — Cold Open",
            line="<strong>For the operating partner</strong> in the first thirty days after close. The MD on the deal team writing the 100-day plan. The portco CFO who needs an auditable answer to <em>where are we leaking money?</em>",
            scroll_to=".ticker",
            actions=[
                lambda p: _spotlight(p, "article:nth-of-type(1)", curtain=True),
                lambda p: _callout(p, "article:nth-of-type(1) .entry-name", "the wedge", "right"),
            ],
        ),
        Beat(
            n="02",
            url=_abs("/finance_output/dx_report_MortgageCo.html"),
            kicker="Beat 2 — The Headline · underline this",
            line="<strong>$1.12B/yr identified.</strong> Three repeatable opportunities. Every dollar traceable to a row in the source data. The line a managing director walks into the next IC with.",
            scroll_to="h1",
            actions=[
                lambda p: _bloom(p, "h1"),
            ],
        ),
        Beat(
            n="03",
            url=_abs("/finance_output/dx_report_MortgageCo.html"),
            kicker="Beat 3 — Why This Exists",
            line="The work this replaces is the first thirty days of a consultant-led diagnostic. <strong>Six weeks. Two hundred grand. A deck.</strong> The output here is the same dollar story, in two minutes, on the operator's laptop.",
            scroll_to="h1",
            actions=[
                lambda p: _spotlight(p, "h1", curtain=True),
                lambda p: _callout(p, "h1", "vs. 6 weeks · McKinsey · $200k", "right"),
            ],
        ),
        Beat(
            n="04",
            url=_abs("/finance_output/dx_report_MortgageCo.html"),
            kicker="Beat 4 — The Numbers Under the Headline",
            line="The stats strip is what an LP asks. <strong>Portco · vertical · EBITDA baseline · total identified · % of EBITDA.</strong> The size of the opportunity is the size of the leakage the prior owner ignored.",
            scroll_to=".meta",
            actions=[
                lambda p: _spotlight(p, ".meta", curtain=True),
            ],
        ),
        Beat(
            n="05",
            url=_abs("/finance_output/dx_report_MortgageCo.html"),
            kicker="Beat 5 — The Contract",
            line="Every figure on this page traces to a source row. <strong>No prose invented. No model arithmetic.</strong> The renderer raises before it ships a number it can't ground. Pandas does the math; the model only narrates.",
            scroll_to=".meta",
            actions=[
                lambda p: _spotlight(p, ".meta", curtain=True),
                lambda p: _callout(p, ".meta", "every figure traces · row IDs in JSON", "right"),
            ],
        ),
        Beat(
            n="06",
            url=_abs("/finance_output/dx_report_MortgageCo.html"),
            kicker="Beat 6 — The First Section · Ranked Opportunities",
            line="<strong>Three opportunities, ranked by counterfactual dollar impact.</strong> Each one — segment definition, annualized dollar, persistence across quarters, the row IDs that built it.",
            scroll_to=".section h2",
            actions=[
                lambda p: _spotlight(p, ".section", curtain=True),
            ],
        ),
        Beat(
            n="07",
            url=_abs("/finance_output/dx_report_MortgageCo.html"),
            kicker="Beat 7 — One Row Drilled",
            line="<strong>$564.78M annualized.</strong> Sub-prime grades on refi loans, persistently negative across four quarters. The CFO can pull the loan IDs from the JSON sidecar and verify every one in their loan-management system.",
            scroll_to=".opp:nth-of-type(1)",
            actions=[
                lambda p: _spotlight(p, ".opp", curtain=True),
                lambda p: _callout(p, ".opp .opp-impact", "row IDs in JSON sidecar", "right"),
            ],
        ),
        Beat(
            n="08",
            url=_abs("/finance_output/dx_report_MortgageCo.html"),
            kicker="Beat 8 — What a Partner Does With It",
            line="<strong>Fifteen seconds for the headline. Two minutes for the per-row evidence. One decision walked into the next meeting.</strong> Not a dashboard the operator checks every Monday. An artifact, scoped for one decision, delivered once.",
            scroll_to=".opp:nth-of-type(1)",
            actions=[
                lambda p: _spotlight(p, ".opp", curtain=True),
                lambda p: _callout(p, ".opp", "15s · 2min · 1 decision", "left"),
            ],
        ),
        Beat(
            n="09",
            url=_abs("/finance_output/explain_MortgageCo_board.html"),
            kicker="Beat 9 — The Next Section · Board Memo",
            line="<strong>The board memo.</strong> Letterpress paper, italic figures, three opportunities laid out for the audience that decides whether to act. Same evidence underneath; language calibrated for the room.",
            scroll_to="h1",
            actions=[
                lambda p: _spotlight(p, "h1", curtain=True),
                lambda p: _callout(p, "h1", "1 page · IC-grade · Wednesday", "right"),
            ],
        ),
        Beat(
            n="10",
            url=_abs("/finance_output/explain_MortgageCo_board.html"),
            kicker="Beat 10 — A Second View · Same Evidence",
            line="The operator sibling carries the same opportunities, language calibrated for the rollout team. <strong>The numbers are identical.</strong> The renderer enforces consistency before either version ships.",
            scroll_to=".opp-section",
            actions=[
                lambda p: _spotlight(p, ".opp-section", curtain=True),
            ],
        ),
        Beat(
            n="11",
            url=_abs("/finance_output/dx_report_MortgageCo.html"),
            kicker="Beat 11 — Reproducibility",
            line="<strong>Same upload twice — identical output.</strong> An LP audit picks up the JSON, walks the row IDs, and rebuilds the entire dollar story from the underlying CSV. This is what defensible looks like.",
            scroll_to=".disclaimer",
            actions=[
                lambda p: _spotlight(p, ".disclaimer", curtain=True),
                lambda p: _callout(p, ".disclaimer", "deterministic · pandas-only", "right"),
            ],
        ),
        Beat(
            n="12",
            url=_abs("/finance_output/dx_report_MortgageCo.html"),
            kicker="Beat 12 — The Colophon",
            line="<strong>The footer names the source artifact, the build command, the methodology link.</strong> Hand the file to anyone — the buyer's diligence VP, an LP audit team — they rebuild it from a clean clone.",
            scroll_to=".footer",
            actions=[
                lambda p: _spotlight(p, ".footer", curtain=True),
            ],
        ),
        Beat(
            n="13",
            url=_abs("/app/tools.html"),
            kicker="Beat 13 — Who Picks It Up Next",
            line="<strong>Three seats.</strong> The deal partner reads the board memo. The operating partner reads the operator memo. The portco CFO verifies against their own system. One tool, three readings, one source of truth.",
            scroll_to=".ticker",
            actions=[
                lambda p: _spotlight(p, "article:nth-of-type(1)", curtain=True),
                lambda p: _callout(p, "article:nth-of-type(1)", "deal partner · ops partner · portco CFO", "right"),
            ],
        ),
        Beat(
            n="14",
            url=_abs("/app/tools.html"),
            kicker="Beat 14 — Outro · Open Source · MIT",
            line="<strong>Tool I of twelve.</strong> The wedge into a portco's data — then move to the moat. Cross-portco benchmark, exit proof pack, eleven more tools on the same engine.",
            scroll_to=".ticker",
            actions=[],
        ),
    ]


# ----------------------------------------------------------------------------
# Renderer
# ----------------------------------------------------------------------------

# ----------------------------------------------------------------------------
# Tool II — Model-to-Narrative Explainer — 14 beats
# ----------------------------------------------------------------------------

def _tool_explainer_beats() -> list[Beat]:
    catalogue = _abs("/app/tools.html")
    memo = _abs("/finance_output/explain_MortgageCo_board.html")
    explainer_card = "article:nth-of-type(2)"  # II — Model-to-Narrative Explainer
    return [
        Beat(
            n="01",
            url=catalogue,
            kicker="II · Model-to-Narrative Explainer · Beat 1 — Cold Open",
            line="<strong>For the deal partner walking into IC.</strong> The MD with eight minutes on the agenda and a page-and-a-half to defend a recommendation. The diagnostic surfaces the dollars; this tool turns them into the memo a partner reads into the record.",
            scroll_to=explainer_card,
            actions=[
                lambda p: _spotlight(p, explainer_card, curtain=True),
                lambda p: _callout(p, f"{explainer_card} form", "drop CSVs · pick audience · render", "right"),
            ],
        ),
        Beat(
            n="02",
            url=memo,
            kicker="Beat 2 — The Headline · underline this",
            line="<strong>Three repeatable opportunities. $1.12B per annum.</strong> Headline plus recommendation in two short paragraphs. The exact sentence the partner reads to the IC, sized for the first page of the deck.",
            scroll_to="h1",
            actions=[
                lambda p: _bloom(p, "h1"),
            ],
        ),
        Beat(
            n="03",
            url=memo,
            kicker="Beat 3 — Why This Exists",
            line="<strong>The reference failure mode is the e-TeleQuote case.</strong> A model said throttle three states; management couldn't defend the <em>why</em> in the boardroom; nothing happened. This tool closes that gap — diagnostic evidence rendered as boardroom prose.",
            scroll_to=".letterhead",
            actions=[
                lambda p: _spotlight(p, ".letterhead", curtain=True),
                lambda p: _callout(p, ".letterhead-meta", "letterpress · Cormorant · oxblood", "left"),
            ],
        ),
        Beat(
            n="04",
            url=memo,
            kicker="Beat 4 — The Numbers Under the Headline",
            line="<strong>Identified · opportunities · vertical · EBITDA base.</strong> Four numbers a partner can recite from memory. The ratio of identified to baseline is the line the IC will challenge first; this is where it lands.",
            scroll_to=".stats-strip",
            actions=[
                lambda p: _spotlight(p, ".stats-strip", curtain=True),
            ],
        ),
        Beat(
            n="05",
            url=memo,
            kicker="Beat 5 — The Contract",
            line="<strong>Every figure in the prose maps to a numeric field in the source OpportunityMap.</strong> The renderer raises before it ships a number it can't ground. Whitelisted prose patterns are explicit — everything else is named source.",
            scroll_to=".summary",
            actions=[
                lambda p: _spotlight(p, ".summary", curtain=True),
                lambda p: _callout(p, ".summary", "no prose invented · figures whitelisted", "right"),
            ],
        ),
        Beat(
            n="06",
            url=memo,
            kicker="Beat 6 — Headline + Recommendation",
            line="<strong>Headline paragraph names the pattern. Recommendation paragraph names the action.</strong> Two paragraphs, one page, the language of an LP letter — not a data-science writeup. The partner reads it once and knows what they're being asked to approve.",
            scroll_to=".summary",
            actions=[
                lambda p: _spotlight(p, ".summary", curtain=True),
            ],
        ),
        Beat(
            n="07",
            url=memo,
            kicker="Beat 7 — One Opportunity Drilled",
            line="<strong>Each opportunity carries four passages</strong> — the why, the counterfactual, the risk of inaction, the rollout. Roman-numeral markers. The counterfactual is the part that survives diligence: it names what changes, with the dollar that flips.",
            scroll_to=".opp-section",
            actions=[
                lambda p: _spotlight(p, ".opp-section", curtain=True),
                lambda p: _callout(p, ".opp-marker", "I · II · III", "left"),
            ],
        ),
        Beat(
            n="08",
            url=memo,
            kicker="Beat 8 — What a Partner Does With It",
            line="<strong>The deal partner walks in with this memo.</strong> The IC member reads it in the seven minutes between agenda items. The chair signs off — or doesn't. Sized for the meeting, not for the reader's leisure.",
            scroll_to=".opp-section",
            actions=[
                lambda p: _spotlight(p, ".opp-section", curtain=True),
                lambda p: _callout(p, ".opp-cohort", "7 min · 1 page · 1 decision", "right"),
            ],
        ),
        Beat(
            n="09",
            url=memo,
            kicker="Beat 9 — The Next Section · Audience Switch",
            line="<strong>Same evidence, different audience.</strong> Toggle to the operator. The language reframes — <em>cohort-level rollout</em> instead of <em>portfolio-level recommendation</em>. The figures don't move; the renderer enforces identity across audiences.",
            scroll_to=".letterhead-meta",
            actions=[
                lambda p: _spotlight(p, ".letterhead-meta", curtain=True),
                lambda p: _callout(p, ".letterhead-meta", "Board → Operator · same evidence", "left"),
            ],
        ),
        Beat(
            n="10",
            url=memo,
            kicker="Beat 10 — A Second View · Operator",
            line="<strong>Operator memo — language calibrated for the rollout team.</strong> The CRO reads it Thursday morning. The same dollar figures appear in the partner's IC memo. The verbs change from <em>recommend</em> to <em>reroute</em>.",
            scroll_to=".opp-section:nth-of-type(2)",
            actions=[
                lambda p: _spotlight(p, ".opp-section:nth-of-type(2), .opp-section", curtain=True),
            ],
        ),
        Beat(
            n="11",
            url=memo,
            kicker="Beat 11 — Reproducibility",
            line="<strong>Re-render against the same OpportunityMap — identical output.</strong> Whitelisted prose patterns are explicit; everything else is grounded in named source fields. An LP picks up the JSON and verifies every figure against the source field it cites.",
            scroll_to=".disclaimer, .colophon, footer",
            actions=[
                lambda p: _spotlight(p, ".disclaimer, footer, .colophon", curtain=True),
                lambda p: _callout(p, ".disclaimer, footer, .colophon", "deterministic · same in, same out", "right"),
            ],
        ),
        Beat(
            n="12",
            url=memo,
            kicker="Beat 12 — The Colophon",
            line="<strong>The footer cites the source OpportunityMap path, the eval scorecard, the build command.</strong> The audit trail travels with the file. Three years from now, an LP can rebuild the memo from the same public commit.",
            scroll_to="footer, .disclaimer, .colophon",
            actions=[
                lambda p: _spotlight(p, "footer, .disclaimer, .colophon", curtain=True),
            ],
        ),
        Beat(
            n="13",
            url=catalogue,
            kicker="Beat 13 — Who Picks It Up Next",
            line="<strong>Three meetings, one memo.</strong> The deal partner takes it to IC. The operating partner takes the sibling version to the rollout meeting. The IR seat takes either to the LP. No inconsistency across them.",
            scroll_to=explainer_card,
            actions=[
                lambda p: _spotlight(p, explainer_card, curtain=True),
                lambda p: _callout(p, explainer_card, "deal partner · ops partner · IR", "right"),
            ],
        ),
        Beat(
            n="14",
            url=catalogue,
            kicker="Beat 14 — Outro · Open Source · MIT",
            line="<strong>Tool II of twelve.</strong> The memo a partner can defend. The audit trail an LP can verify. Eleven more tools on the same evidence chain.",
            scroll_to=".ticker",
            actions=[],
        ),
    ]


# ----------------------------------------------------------------------------
# Tool III — Cross-Portco Benchmarking (BX) — 14 beats
# ----------------------------------------------------------------------------

def _tool_bx_beats() -> list[Beat]:
    catalogue = _abs("/app/tools.html")
    report = _abs("/finance_output/bx_report_mixed_fund.html")
    bx_card = "article:nth-of-type(3)"  # III — Cross-Portco Benchmarking
    return [
        Beat(
            n="01",
            url=catalogue,
            kicker="III · Cross-Portco Benchmarking · Beat 1 — Cold Open",
            line="<strong>For the managing partner writing the LP letter.</strong> The fund operating partner asked: <em>is this pattern fund-wide or one-portco?</em> The IR seat drafting the operational-alpha exhibit.",
            scroll_to=bx_card,
            actions=[
                lambda p: _spotlight(p, bx_card, curtain=True),
                lambda p: _callout(p, f"{bx_card} form", "preset · or list portco IDs", "right"),
            ],
        ),
        Beat(
            n="02",
            url=report,
            kicker="Beat 2 — The Headline · underline this",
            line="<strong>Seven portfolio companies. $1.14B of identifiable annualized impact.</strong> The number that goes on page two of the LP letter under <em>operational alpha</em> — and the number that wasn't there before this stack.",
            scroll_to="h1",
            actions=[
                lambda p: _bloom(p, "h1"),
            ],
        ),
        Beat(
            n="03",
            url=report,
            kicker="Beat 3 — Why This Exists",
            line="<strong>Fund partners run cross-portco benchmarks today by hand</strong> — Excel, PDFs in different formats. This tool reads the diagnostic JSON each portco already produced and rolls them up into one comparable view: P10, median, P90, percentile per portco.",
            scroll_to="h1",
            actions=[
                lambda p: _spotlight(p, "h1", curtain=True),
                lambda p: _callout(p, "h1", "vs. Excel · vs. scorecard vendor", "right"),
            ],
        ),
        Beat(
            n="04",
            url=report,
            kicker="Beat 4 — The Numbers Under the Headline",
            line="<strong>Corpus · portco count · total identified · opportunity count.</strong> Four numbers the LP report needs verbatim. Twenty-one named patterns across the fund — each defendable, each citable.",
            scroll_to=".meta",
            actions=[
                lambda p: _spotlight(p, ".meta", curtain=True),
            ],
        ),
        Beat(
            n="05",
            url=report,
            kicker="Beat 5 — The Contract",
            line="<strong>Every per-portco contribution traces to that portco's OpportunityMap JSON.</strong> Roll a number up, walk it back to the row evidence — same number both directions. The renderer enforces it.",
            scroll_to=".meta",
            actions=[
                lambda p: _spotlight(p, ".meta", curtain=True),
                lambda p: _callout(p, ".meta", "rolls up · walks back · same number", "right"),
            ],
        ),
        Beat(
            n="06",
            url=report,
            kicker="Beat 6 — The First Section · Archetype Distribution",
            line="<strong>Pricing · Selection · Allocation · Routing · Timing.</strong> Five decision archetypes the engine recognizes across any vertical. P10, median, P90 per archetype — the fund is its own benchmark, not a vendor's.",
            scroll_to=".arche-grid",
            actions=[
                lambda p: _spotlight(p, ".arche-grid", curtain=True),
            ],
        ),
        Beat(
            n="07",
            url=report,
            kicker="Beat 7 — One Archetype Drilled · Pricing",
            line="<strong>Pricing leads at $573M across all seven portcos.</strong> The same pattern appears in three lending portcos and four mortgage portcos. That's a fund theme, not a portco anomaly — the LP letter can name it.",
            scroll_to=".arche-row",
            actions=[
                lambda p: _spotlight(p, ".arche-row:nth-of-type(2), .arche-row", curtain=True),
                lambda p: _callout(p, ".arche-row", "fund-wide · 7 of 7 · structural", "right"),
            ],
        ),
        Beat(
            n="08",
            url=report,
            kicker="Beat 8 — What a Partner Does With It",
            line="<strong>Read the bars. Spot the patterns that repeat. Name the fund theme.</strong> The managing partner gets a one-page exhibit out of this — the operational-alpha page that wasn't in the prior vintage's LP letter.",
            scroll_to=".arche-grid",
            actions=[
                lambda p: _spotlight(p, ".arche-grid", curtain=True),
                lambda p: _callout(p, ".arche-grid", "one-page · LP-grade · page 2", "left"),
            ],
        ),
        Beat(
            n="09",
            url=report,
            kicker="Beat 9 — The Next Section · Rank Table",
            line="<strong>Portcos ranked by total identifiable impact.</strong> Each row carries a fund percentile. MortgageCo at $1.12B; consumer regionals at the bottom — under a million each. The percentile is the right metric, not the absolute. Mortgage scale dominates.",
            scroll_to=".rank-tbl",
            actions=[
                lambda p: _spotlight(p, ".rank-tbl", curtain=True),
            ],
        ),
        Beat(
            n="10",
            url=report,
            kicker="Beat 10 — A Second View · Peer Groups",
            line="<strong>Cosine similarity on the archetype-shape vector.</strong> Portcos with similar decision-quality profiles cluster together — even across verticals. The cluster names the fund theme. Mortgage in one cluster; consumer regionals in another.",
            scroll_to=".peer-card",
            actions=[
                lambda p: _spotlight(p, ".peer-card", curtain=True),
                lambda p: _callout(p, ".peer-card", "shape · not scale · the right discriminator", "right"),
            ],
        ),
        Beat(
            n="11",
            url=report,
            kicker="Beat 11 — Reproducibility",
            line="<strong>Rebuild the corpus from the per-portco JSONs — same archetype distribution, same rank, same clusters.</strong> The LP audit team pulls the public repo, the public datasets, and rebuilds the fund-level exhibit in fifteen seconds.",
            scroll_to=".disclaimer, footer",
            actions=[
                lambda p: _spotlight(p, ".disclaimer, footer", curtain=True),
                lambda p: _callout(p, ".disclaimer, footer", "deterministic · 15s rebuild", "right"),
            ],
        ),
        Beat(
            n="12",
            url=report,
            kicker="Beat 12 — The Colophon",
            line="<strong>The footer names the corpus build command, every portco JSON path, the methodology link.</strong> Three years from now, a future LP audit rebuilds the exhibit from the same commit. The artifact travels with the audit trail.",
            scroll_to="footer, .disclaimer",
            actions=[
                lambda p: _spotlight(p, "footer, .disclaimer", curtain=True),
            ],
        ),
        Beat(
            n="13",
            url=catalogue,
            kicker="Beat 13 — Who Picks It Up Next",
            line="<strong>Three readers.</strong> The managing partner takes the headline to the LP letter. The operating partner takes the rank table to the next portfolio review. The IR seat takes the peer-group exhibit to the next allocation conversation.",
            scroll_to=bx_card,
            actions=[
                lambda p: _spotlight(p, bx_card, curtain=True),
                lambda p: _callout(p, bx_card, "MP · ops partner · IR", "right"),
            ],
        ),
        Beat(
            n="14",
            url=catalogue,
            kicker="Beat 14 — Outro · Open Source · MIT",
            line="<strong>Tool III of twelve.</strong> The wedge into a portco. The moat across the fund. Same toolchain. Same audit trail. Pandas-deterministic.",
            scroll_to=".ticker",
            actions=[],
        ),
    ]


# ----------------------------------------------------------------------------
# Tool IV — LLM Eval for PE — 14 beats
# ----------------------------------------------------------------------------

def _tool_eval_beats() -> list[Beat]:
    catalogue = _abs("/app/tools.html")
    report = _abs("/finance_output/eval_corpus_summary.html")
    eval_card = "article:nth-of-type(4)"  # IV — LLM Eval for PE
    return [
        Beat(
            n="01",
            url=catalogue,
            kicker="IV · LLM Eval for PE · Beat 1 — Cold Open",
            line="<strong>For the head of data at the fund.</strong> The compliance seat reviewing any AI-generated artifact before it reaches LPs. The IC member who wants to know whether to trust the memo.",
            scroll_to=eval_card,
            actions=[
                lambda p: _spotlight(p, eval_card, curtain=True),
                lambda p: _callout(p, f"{eval_card} form", "no inputs · runs on the corpus", "right"),
            ],
        ),
        Beat(
            n="02",
            url=report,
            kicker="Beat 2 — The Headline · underline this",
            line="<strong>13 memos scored against 13 OpportunityMap sources.</strong> Citation accuracy. Hallucination rate. Coverage. Consistency. Four numbers, one rubric, deterministic scoring — the QA layer every memo crosses before an LP sees it.",
            scroll_to="h1",
            actions=[
                lambda p: _bloom(p, "h1"),
            ],
        ),
        Beat(
            n="03",
            url=report,
            kicker="Beat 3 — Why This Exists",
            line="<strong>PE firms ship AI-generated memos to LPs today with no eval at all.</strong> The rubric is invisible. The failure mode is invisible. The audit is impossible. This tool puts the audit in the file — the score sits at the top of every memo it passes.",
            scroll_to=".stats-strip",
            actions=[
                lambda p: _spotlight(p, ".stats-strip", curtain=True),
                lambda p: _callout(p, ".stats-strip", "audit-grade · vs. invisible rubric", "right"),
            ],
        ),
        Beat(
            n="04",
            url=report,
            kicker="Beat 4 — The Numbers Under the Headline",
            line="<strong>87% mean citation accuracy. 100% coverage. 100% consistency. 31% hallucination — and every flagged token is named, not hand-waved.</strong>",
            scroll_to=".stats-strip",
            actions=[
                lambda p: _spotlight(p, ".stats-strip", curtain=True),
            ],
        ),
        Beat(
            n="05",
            url=report,
            kicker="Beat 5 — The Contract",
            line="<strong>Same memo and same source in, same score out — every time.</strong> Deterministic by construction. No LLM at runtime in the scoring layer; pure pandas plus regex. The eval is the contract every memo crosses before it reaches an LP.",
            scroll_to=".stats-strip",
            actions=[
                lambda p: _spotlight(p, ".stats-strip", curtain=True),
                lambda p: _callout(p, ".stats-strip", "pandas + regex · same in, same out", "right"),
            ],
        ),
        Beat(
            n="06",
            url=report,
            kicker="Beat 6 — The First Section · Per-Memo Grid",
            line="<strong>13 memos. 4 columns. The DC Mortgage memo at the top — perfect citation, perfect coverage.</strong> The HMDA Delaware memo at the bottom — five of fifteen figures cited. The rubric caught something specific.",
            scroll_to="table",
            actions=[
                lambda p: _spotlight(p, "table", curtain=True),
            ],
        ),
        Beat(
            n="07",
            url=report,
            kicker="Beat 7 — One Memo Drilled · DCMortgage",
            line="<strong>DC Mortgage — 1.00 citation, 1.00 coverage, 1.00 consistency.</strong> Three of three opportunities cited; fifteen of fifteen figures grounded. This is the contract a memo passes before it ships.",
            scroll_to="table tbody tr:first-of-type",
            actions=[
                lambda p: _spotlight(p, "table tbody tr:first-of-type", curtain=True),
                lambda p: _callout(p, "table tbody tr:first-of-type", "1.00 across all four", "right"),
            ],
        ),
        Beat(
            n="08",
            url=report,
            kicker="Beat 8 — What a Partner Does With It",
            line="<strong>The compliance seat reads this in fifteen seconds.</strong> The IC member reads it before the underlying memo. If the citation score is below 70, the memo doesn't reach the meeting. The rubric is the gate.",
            scroll_to=".stats-strip",
            actions=[
                lambda p: _spotlight(p, ".stats-strip", curtain=True),
                lambda p: _callout(p, ".stats-strip", "below 70% citation = doesn't ship", "left"),
            ],
        ),
        Beat(
            n="09",
            url=report,
            kicker="Beat 9 — The Next Section · Hallucination Detail",
            line="<strong>Hallucination counts entities — bold-named cohorts, dates, named segments — that appear in the memo but not in the source.</strong> Whitelisted playbook copy gets flagged. That's why the rate sits around 30% on deterministic templates — and that's the right behavior.",
            scroll_to="table",
            actions=[
                lambda p: _spotlight(p, "table", curtain=True),
            ],
        ),
        Beat(
            n="10",
            url=report,
            kicker="Beat 10 — A Second View · Coverage",
            line="<strong>Coverage: how many of the source's opportunities did the memo address.</strong> All thirteen memos hit 100%. The renderer enforces this — a memo can't skip an opportunity from the source. The contract goes both ways.",
            scroll_to="table",
            actions=[
                lambda p: _spotlight(p, "table", curtain=True),
                lambda p: _callout(p, "table", "renderer enforces · both ways", "right"),
            ],
        ),
        Beat(
            n="11",
            url=report,
            kicker="Beat 11 — Reproducibility",
            line="<strong>Run the eval on a competitor's output. Run it on a vendor's draft. Run it on last quarter's memo.</strong> Same corpus in, same scores out. The tool turns <em>can we trust this?</em> into a number — not a vibe.",
            scroll_to=".disclaimer, footer",
            actions=[
                lambda p: _spotlight(p, ".disclaimer, footer", curtain=True),
                lambda p: _callout(p, ".disclaimer, footer", "deterministic · same in, same out", "right"),
            ],
        ),
        Beat(
            n="12",
            url=report,
            kicker="Beat 12 — The Colophon",
            line="<strong>Footer names the rubric module, the corpus directory, the build command.</strong> The methodology is public. Anyone with the public commit can reproduce every score in the report — including the calibration findings.",
            scroll_to="footer, .disclaimer",
            actions=[
                lambda p: _spotlight(p, "footer, .disclaimer", curtain=True),
            ],
        ),
        Beat(
            n="13",
            url=catalogue,
            kicker="Beat 13 — Who Picks It Up Next",
            line="<strong>Three seats.</strong> The head of data signs off. The compliance seat files the scorecard. The IC member opens the underlying memo only after the score clears the threshold.",
            scroll_to=eval_card,
            actions=[
                lambda p: _spotlight(p, eval_card, curtain=True),
                lambda p: _callout(p, eval_card, "head of data · compliance · IC", "right"),
            ],
        ),
        Beat(
            n="14",
            url=catalogue,
            kicker="Beat 14 — Outro · Open Source · MIT",
            line="<strong>Tool IV of twelve.</strong> Every memo this stack ships goes through this harness before it reaches an LP. The eval is the contract.",
            scroll_to=".ticker",
            actions=[],
        ),
    ]


# ----------------------------------------------------------------------------
# Tool V — CIM Red-Flag Extractor — 14 beats
# ----------------------------------------------------------------------------

def _tool_cim_beats() -> list[Beat]:
    catalogue = _abs("/app/tools.html")
    report = _abs("/finance_output/cim_redflags_SHC.html")
    card = "article:nth-of-type(5)"
    return [
        Beat("01", catalogue, "V · CIM Red-Flag Extractor · Beat 1 — Cold Open",
             "<strong>For the diligence VP reading a 500-page CIM the night before IC.</strong> The associate building the red-flag schedule for the partner. The deal team that needs a defensible <em>list</em>.",
             scroll_to=card,
             actions=[lambda p: _spotlight(p, card, True),
                      lambda p: _callout(p, f"{card} form", "ticker · 10-K · 10-Q", "right")]),
        Beat("02", report, "Beat 2 — The Headline · underline this",
             "<strong>49 red flags surfaced across 17 sections of the Sotera Health 10-K.</strong> 16 high-severity. 32 medium. 1 low. Each flag carries a section pointer and a one-to-two sentence excerpt.",
             scroll_to="h1",
             actions=[lambda p: _bloom(p, "h1")]),
        Beat("03", report, "Beat 3 — Why This Exists",
             "<strong>The associate spends two nights flipping pages with a highlighter.</strong> This tool extracts the things in seconds — every flag carries a citation a reviewer can verify in the source filing in fifteen seconds.",
             scroll_to=".lede, h1",
             actions=[lambda p: _spotlight(p, ".lede, h1", True),
                      lambda p: _callout(p, ".lede, h1", "vs. 2 nights · vs. highlighter", "right")]),
        Beat("04", report, "Beat 4 — The Numbers Under the Headline",
             "<strong>Total flags · high · medium · low.</strong> The four numbers the partner asks for at the top of the IC meeting. Severity is heuristic — calibrated for diligence-priority <em>recall</em>, not precision.",
             scroll_to=".stats-strip",
             actions=[lambda p: _spotlight(p, ".stats-strip", True)]),
        Beat("05", report, "Beat 5 — The Contract",
             "<strong>Every flag has a section citation and a verbatim excerpt.</strong> No model paraphrase. No invented prose. Run twice on the same filing — identical output. Regex math, deterministic contract.",
             scroll_to=".stats-strip",
             actions=[lambda p: _spotlight(p, ".stats-strip", True),
                      lambda p: _callout(p, ".stats-strip", "regex · verbatim · deterministic", "right")]),
        Beat("06", report, "Beat 6 — The First Section · Flag List",
             "<strong>49 flags fit on one page.</strong> The diligence schedule writes itself. The questions for the seller's banker write themselves. The partner reads one page and walks into IC with a list, not a hunch.",
             scroll_to=".flag",
             actions=[lambda p: _spotlight(p, ".flag", True)]),
        Beat("07", report, "Beat 7 — One Flag Drilled · High-Severity",
             "<strong>The first flag.</strong> Material weakness in internal control over financial reporting — Item 9A. Excerpt printed verbatim. The deal team verifies the citation against the source filing in fifteen seconds.",
             scroll_to=".flag.high",
             actions=[lambda p: _spotlight(p, ".flag.high, .flag", True),
                      lambda p: _callout(p, ".flag.high, .flag", "verbatim · section-cited · 15s verify", "right")]),
        Beat("08", report, "Beat 8 — What a Partner Does With It",
             "<strong>The partner reads the schedule, marks three flags for follow-up, sends the rest to the associate.</strong> The schedule becomes the LOI red-flag exhibit.",
             scroll_to=".flag",
             actions=[lambda p: _spotlight(p, ".flag", True),
                      lambda p: _callout(p, ".flag", "1 page · 3 follow-ups · IC-grade", "left")]),
        Beat("09", report, "Beat 9 — The Next Section · Flag Families",
             "<strong>Eight flag families:</strong> customer concentration, going concern, material weakness, goodwill impairment, auditor change, related-party transactions, restatement, severe-language risk factors. Standard PE diligence checklist, automated.",
             scroll_to=".flag.medium, .flag",
             actions=[lambda p: _spotlight(p, ".flag.medium, .flag", True)]),
        Beat("10", report, "Beat 10 — A Second View · Severity-Language Scan",
             "<strong>Severity-language scan picks up paragraphs with three-or-more <em>material, substantial, adverse</em> hits in Item 1A.</strong> The operator's own self-disclosed top concerns, stack-ranked by their own emphasis.",
             scroll_to=".flag",
             actions=[lambda p: _spotlight(p, ".flag", True),
                      lambda p: _callout(p, ".flag", "self-disclosed · honest signal", "right")]),
        Beat("11", report, "Beat 11 — Reproducibility",
             "<strong>Eight extractors, deterministic, no LLM at runtime.</strong> Run twice on the same filing — identical output. The math is regex; the contract is reproducibility.",
             scroll_to=".colophon, footer",
             actions=[lambda p: _spotlight(p, ".colophon, footer", True),
                      lambda p: _callout(p, ".colophon, footer", "regex · no LLM · same in, same out", "right")]),
        Beat("12", report, "Beat 12 — The Colophon",
             "<strong>Footer names the source filing, the EDGAR fetch URL, the build command.</strong> Hand the report to the seller's banker — they pull the same filing and verify every excerpt. Adversarial-grade defensibility.",
             scroll_to=".colophon, footer",
             actions=[lambda p: _spotlight(p, ".colophon, footer", True)]),
        Beat("13", catalogue, "Beat 13 — Who Picks It Up Next",
             "<strong>The associate verifies. The partner approves.</strong> The deal team takes the schedule into IC. The seller's banker gets the redacted version. One page replaces two associate-nights.",
             scroll_to=card,
             actions=[lambda p: _spotlight(p, card, True),
                      lambda p: _callout(p, card, "associate · partner · banker", "right")]),
        Beat("14", catalogue, "Beat 14 — Outro · Open Source · MIT",
             "<strong>Tool V of twelve.</strong> Two associate-nights collapsed into ninety seconds. The partner still owns the call — they just stop owning the page-flipping.",
             scroll_to=".ticker", actions=[]),
    ]


# ----------------------------------------------------------------------------
# Tool VI — Seller-Side AI Diligence Pack — 14 beats
# ----------------------------------------------------------------------------

def _tool_seller_pack_beats() -> list[Beat]:
    catalogue = _abs("/app/tools.html")
    report = _abs("/finance_output/exit_proof_pack_MortgageCo.html")
    card = "article:nth-of-type(6)"
    return [
        Beat("01", catalogue, "VI · Seller-Side AI Diligence Pack · Beat 1 — Cold Open",
             "<strong>For the deal partner at exit.</strong> The banker engaged to run the sale. The portco CFO who has to defend the IM's AI-EBITDA number to a buyer who <em>will</em> try to take it back.",
             scroll_to=card,
             actions=[lambda p: _spotlight(p, card, True),
                      lambda p: _callout(p, f"{card} form", "drop CSVs · DX runs · proof pack", "right")]),
        Beat("02", report, "Beat 2 — The Headline · underline this",
             "<strong>$1.12B base · sensitivity $560M – $1.46B · three documented claims.</strong> The number the seller defends. The number the buyer tries to take back.",
             scroll_to=".headline-figure, h1",
             actions=[lambda p: _bloom(p, ".headline-figure, h1")]),
        Beat("03", report, "Beat 3 — Why This Exists",
             "<strong>AlixPartners shipped a buyer-side AI Disruption Score.</strong> Buyers are walking from deals or discounting based on it. Sellers had no equivalent — until now.",
             scroll_to=".headline, h1",
             actions=[lambda p: _spotlight(p, ".headline, h1", True),
                      lambda p: _callout(p, ".headline, h1", "vs. AlixPartners buyer-side score", "right")]),
        Beat("04", report, "Beat 4 — The Numbers Under the Headline",
             "<strong>Base · conservative · aggressive · documented claims.</strong> The buyer's diligence VP recites these from this page. Conservative survives the worst-case challenge; aggressive is the upside the banker quotes.",
             scroll_to=".headline-range, .headline",
             actions=[lambda p: _spotlight(p, ".headline-range, .headline", True)]),
        Beat("05", report, "Beat 5 — The Contract",
             "<strong>Every claim ties to a DX OpportunityMap row.</strong> Conservative is 50% of base; aggressive is 130%. Multipliers are frozen module-level constants. The buyer can re-derive the table from the same source data.",
             scroll_to=".headline-range, .headline",
             actions=[lambda p: _spotlight(p, ".headline-range, .headline", True),
                      lambda p: _callout(p, ".headline-range, .headline", "50% / 100% / 130% · frozen", "right")]),
        Beat("06", report, "Beat 6 — The First Section · Provenance Ledger",
             "<strong>Per-claim provenance.</strong> The claim, the source field, the row count behind it, the model assumption that turned the field into dollars.",
             scroll_to=".provenance, .check-list",
             actions=[lambda p: _spotlight(p, ".provenance, .check-list", True)]),
        Beat("07", report, "Beat 7 — One Claim Drilled · The Top Row",
             "<strong>The top claim. $564M annualized.</strong> Source field — <code>loans.csv</code>, column <code>grade</code>. Row count — 8,700. The assumption — break-even routing recovers the cohort. Every link is named.",
             scroll_to=".check-block, .provenance",
             actions=[lambda p: _spotlight(p, ".check-block, .provenance", True),
                      lambda p: _callout(p, ".check-block, .provenance", "field · rows · assumption · all named", "right")]),
        Beat("08", report, "Beat 8 — What a Partner Does With It",
             "<strong>The seller's deal partner walks into LOI with this pack already in the data room.</strong> The buyer's diligence opens the file, walks the rows, signs off without renegotiation. The 10% haircut at signing doesn't happen.",
             scroll_to=".provenance",
             actions=[lambda p: _spotlight(p, ".provenance", True),
                      lambda p: _callout(p, ".provenance", "no haircut at LOI · pre-disclosed", "left")]),
        Beat("09", report, "Beat 9 — The Next Section · Methodology",
             "<strong>Seven-stage pipeline named explicitly.</strong> Pandas-deterministic. Model never touches arithmetic. The methodology page is not a marketing claim — it's a schedule to the SPA.",
             scroll_to=".method",
             actions=[lambda p: _spotlight(p, ".method", True)]),
        Beat("10", report, "Beat 10 — A Second View · Defensibility Checklist",
             "<strong>Six defensibility tests.</strong> Counterfactual is not the baseline. Persistence is multi-quarter. Evidence is row-level. No claim survives that doesn't pass all six.",
             scroll_to=".check-list",
             actions=[lambda p: _spotlight(p, ".check-list", True),
                      lambda p: _callout(p, ".check-list", "6 tests · all pass · or excluded", "right")]),
        Beat("11", report, "Beat 11 — Reproducibility",
             "<strong>Buyer rebuilds the pack from the public repo plus the data room CSVs — identical output.</strong> The seller's claim survives the adversarial rebuild.",
             scroll_to=".colophon, footer",
             actions=[lambda p: _spotlight(p, ".colophon, footer", True),
                      lambda p: _callout(p, ".colophon, footer", "adversarial-grade · same output", "right")]),
        Beat("12", report, "Beat 12 — The Colophon",
             "<strong>Footer names the source DX OpportunityMap, the build command, the sensitivity multiplier constants.</strong> Three years from now, an arbitrator can rebuild the seller's claim from the same commit.",
             scroll_to=".colophon, footer",
             actions=[lambda p: _spotlight(p, ".colophon, footer", True)]),
        Beat("13", catalogue, "Beat 13 — Who Picks It Up Next",
             "<strong>The seller's deal partner files this on day one.</strong> The banker includes it in the IM appendix. The buyer's diligence VP opens it, walks the ledger, signs off.",
             scroll_to=card,
             actions=[lambda p: _spotlight(p, card, True),
                      lambda p: _callout(p, card, "seller · banker · buyer", "right")]),
        Beat("14", catalogue, "Beat 14 — Outro · Open Source · MIT",
             "<strong>Tool VI of twelve.</strong> The pack the seller's deal team wants on file before banker engagement. The pack the buyer's deal team wishes more sellers shipped.",
             scroll_to=".ticker", actions=[]),
    ]


# ----------------------------------------------------------------------------
# Tool VII — DDQ Automation + Consistency — 14 beats
# ----------------------------------------------------------------------------

def _tool_ddq_beats() -> list[Beat]:
    catalogue = _abs("/app/tools.html")
    report = _abs("/finance_output/ddq_response_Bolnet_Capital_Partners_I.html")
    card = "article:nth-of-type(7)"
    return [
        Beat("01", catalogue, "VII · DDQ Automation + Consistency · Beat 1 — Cold Open",
             "<strong>For the IR seat staring down a 40-question ILPA AI DDQ at 11pm.</strong> The CFO at the fund who has to sign the answers. The LP drafting the questions in the first place.",
             scroll_to=card,
             actions=[lambda p: _spotlight(p, card, True),
                      lambda p: _callout(p, f"{card} form", "fund name · walks evidence", "right")]),
        Beat("02", report, "Beat 2 — The Headline · underline this",
             "<strong>12 questions answered. 38 artifacts indexed across 14 portcos. 26 cross-answer consistency checks.</strong> Every answer cites the file it came from.",
             scroll_to="h1",
             actions=[lambda p: _bloom(p, "h1")]),
        Beat("03", report, "Beat 3 — Why This Exists",
             "<strong>ILPA released DDQ v2.0 in Q1.</strong> New AI governance, data lineage, model risk, vendor management sections. Funds answer them by hand, inconsistently. The first GP with a consistency layer wins the next allocation cycle.",
             scroll_to=".lede, h1",
             actions=[lambda p: _spotlight(p, ".lede, h1", True),
                      lambda p: _callout(p, ".lede, h1", "vs. by-hand · vs. inconsistent", "right")]),
        Beat("04", report, "Beat 4 — The Numbers Under the Headline",
             "<strong>Questions · artifacts · flags · portcos.</strong> The artifacts column is the proof — every answer pulls from a real file in the fund's archive, not a model invention.",
             scroll_to=".stats-strip",
             actions=[lambda p: _spotlight(p, ".stats-strip", True)]),
        Beat("05", report, "Beat 5 — The Contract",
             "<strong>Retrieval is regex-based, deterministic — same archive in, same answers out.</strong> No model fabrication; the LLM only stitches the retrieved evidence into ILPA-shaped prose.",
             scroll_to=".stats-strip",
             actions=[lambda p: _spotlight(p, ".stats-strip", True),
                      lambda p: _callout(p, ".stats-strip", "regex · cited · auditable", "right")]),
        Beat("06", report, "Beat 6 — The First Section · Q01 Governance",
             "<strong>Question one: provide an inventory of AI / ML systems across the portfolio.</strong> The answer pulls from agent-sprawl, normalization, every per-portco DX run.",
             scroll_to=".question-block",
             actions=[lambda p: _spotlight(p, ".question-block", True)]),
        Beat("07", report, "Beat 7 — One Answer Drilled",
             "<strong>The answer cites 38 files.</strong> Each citation is a path, verifiable against the working directory. No model imagination between question and evidence.",
             scroll_to=".citations, .question-block",
             actions=[lambda p: _spotlight(p, ".citations, .question-block", True),
                      lambda p: _callout(p, ".citations, .question-block", "38 paths · all verifiable", "right")]),
        Beat("08", report, "Beat 8 — What a Partner Does With It",
             "<strong>The IR seat reviews and edits — first-draft is the contract, not the final word.</strong> The win is consistency: no two answers contain different numbers about the same fact.",
             scroll_to=".question-block",
             actions=[lambda p: _spotlight(p, ".question-block", True),
                      lambda p: _callout(p, ".question-block", "first-draft · review · sign · ship", "left")]),
        Beat("09", report, "Beat 9 — The Next Section · Consistency Checker",
             "<strong>26 consistency checks.</strong> Numeric mismatch is high-severity. Entity-orphan is medium. The fund-wide AI EBITDA in Q3 has to match the sum of per-portco AI EBITDA in Q5.",
             scroll_to=".flag-block",
             actions=[lambda p: _spotlight(p, ".flag-block", True)]),
        Beat("10", report, "Beat 10 — A Second View · One Flag Drilled",
             "<strong>One flag.</strong> Q1 cites 14 portcos. Q9 cites 2. The IR analyst sees the discrepancy with the citation and knows which to fix — surfaced before send.",
             scroll_to=".flag-block",
             actions=[lambda p: _spotlight(p, ".flag-block", True),
                      lambda p: _callout(p, ".flag-block", "before send · before LP · before audit", "right")]),
        Beat("11", report, "Beat 11 — Reproducibility",
             "<strong>Re-run against the same archive — identical output.</strong> New artifact added — the next run picks it up automatically. The packet evolves with the fund's evidence.",
             scroll_to=".colophon, footer",
             actions=[lambda p: _spotlight(p, ".colophon, footer", True),
                      lambda p: _callout(p, ".colophon, footer", "deterministic · live archive", "right")]),
        Beat("12", report, "Beat 12 — The Colophon",
             "<strong>Footer names the question set, the archive directory, the build command.</strong> The question set is frozen in a Python module — auditable, customizable per fund.",
             scroll_to=".colophon, footer",
             actions=[lambda p: _spotlight(p, ".colophon, footer", True)]),
        Beat("13", catalogue, "Beat 13 — Who Picks It Up Next",
             "<strong>The IR analyst edits and signs.</strong> The CFO countersigns. The compliance seat files the consistency report alongside. The LP opens a packet that already passed its own internal audit.",
             scroll_to=card,
             actions=[lambda p: _spotlight(p, card, True),
                      lambda p: _callout(p, card, "IR · CFO · compliance · LP", "right")]),
        Beat("14", catalogue, "Beat 14 — Outro · Open Source · MIT",
             "<strong>Tool VII of twelve.</strong> The first GP with a consistency layer wins the next allocation cycle.",
             scroll_to=".ticker", actions=[]),
    ]


# ----------------------------------------------------------------------------
# Tool VIII — Portfolio Normalization — 14 beats
# ----------------------------------------------------------------------------

def _tool_normalize_beats() -> list[Beat]:
    catalogue = _abs("/app/tools.html")
    report = _abs("/finance_output/normalize_3portcos.html")
    card = "article:nth-of-type(8)"
    return [
        Beat("01", catalogue, "VIII · Portfolio Normalization · Beat 1 — Cold Open",
             "<strong>For the operating partner running portfolio analytics.</strong> The ops associate building the cross-portco rollup. The CFO at a portco whose chart of accounts looks nothing like the next portco's.",
             scroll_to=card,
             actions=[lambda p: _spotlight(p, card, True),
                      lambda p: _callout(p, f"{card} form", "drop N CSVs · type IDs", "right")]),
        Beat("02", report, "Beat 2 — The Headline · underline this",
             "<strong>3 portcos folded · 195,473 rows normalized into 10 canonical fields · 9 anomalies flagged.</strong> Defensible roll-up, every cell tracing home.",
             scroll_to="h1",
             actions=[lambda p: _bloom(p, "h1")]),
        Beat("03", report, "Beat 3 — Why This Exists",
             "<strong>Operating partners get N portcos' P&Ls every month, each in a different format.</strong> Different chart-of-accounts, different period definitions, different revenue recognition columns. They normalize by hand. This kills the ritual.",
             scroll_to=".lede, h1",
             actions=[lambda p: _spotlight(p, ".lede, h1", True),
                      lambda p: _callout(p, ".lede, h1", "vs. by-hand · vs. 3hr exercise", "right")]),
        Beat("04", report, "Beat 4 — The Numbers Under the Headline",
             "<strong>Portcos folded · rows normalized · canonical fields · anomalies flagged.</strong> Nine flags is a conversation; ninety would be a refusal.",
             scroll_to=".stats-strip",
             actions=[lambda p: _spotlight(p, ".stats-strip", True)]),
        Beat("05", report, "Beat 5 — The Contract",
             "<strong>Three precedence layers — alias lookup, regex match, fuzzy token-Jaccard.</strong> Every match logged with confidence score in the audit JSON. Same input, same mapping.",
             scroll_to=".stats-strip",
             actions=[lambda p: _spotlight(p, ".stats-strip", True),
                      lambda p: _callout(p, ".stats-strip", "alias · regex · fuzzy · all logged", "right")]),
        Beat("06", report, "Beat 6 — The First Section · Per-Portco Mapping",
             "<strong>Per-portco source-field mapping, named explicitly.</strong> Midwest calls it <code>principal</code>; Pacific calls it <code>loan_amt</code>; canonical calls it <code>loan_amount</code>. The mapping is in the file.",
             scroll_to=".ledger",
             actions=[lambda p: _spotlight(p, ".ledger", True)]),
        Beat("07", report, "Beat 7 — One Mapping Drilled",
             "<strong>Yasserh column <code>loan_amount</code> mapped to canonical <code>loan_amount</code>, confidence 1.00.</strong> Method: alias lookup. Fuzzy match only kicks in when alias and regex both miss.",
             scroll_to=".pill.alias, .ledger",
             actions=[lambda p: _spotlight(p, ".pill.alias, .ledger", True),
                      lambda p: _callout(p, ".pill.alias, .ledger", "alias · 1.00 · clean match", "right")]),
        Beat("08", report, "Beat 8 — What a Partner Does With It",
             "<strong>The ops associate verifies the mapping in fifteen seconds.</strong> The operating partner reads only the anomaly section. The portco CFO verifies their own mapping rows. Every seat sees the part of the audit that's theirs.",
             scroll_to=".ledger",
             actions=[lambda p: _spotlight(p, ".ledger", True),
                      lambda p: _callout(p, ".ledger", "associate · partner · CFO · all served", "left")]),
        Beat("09", report, "Beat 9 — The Next Section · Anomalies",
             "<strong>9 anomalies surfaced.</strong> Two portcos report APR; one reports rate only. One portco reports DPD as days; one as buckets. Three-axis detector: magnitude, sign-flip, coverage.",
             scroll_to=".anomaly",
             actions=[lambda p: _spotlight(p, ".anomaly", True)]),
        Beat("10", report, "Beat 10 — A Second View · One Anomaly Drilled",
             "<strong>Midwest median loan size — $12k. Yasserh median — $296k.</strong> A 24× scale gap. Not a unit error; a product-mix difference. Surfaced; named; treated correctly in the rollup.",
             scroll_to=".anomaly",
             actions=[lambda p: _spotlight(p, ".anomaly", True),
                      lambda p: _callout(p, ".anomaly", "24× gap · product mix · not error", "right")]),
        Beat("11", report, "Beat 11 — Reproducibility",
             "<strong>Mapping audit JSON is the audit trail.</strong> Open it, see exactly which source column became which canonical field, with confidence score and method. End-to-end auditable.",
             scroll_to=".colophon, footer",
             actions=[lambda p: _spotlight(p, ".colophon, footer", True),
                      lambda p: _callout(p, ".colophon, footer", "JSON · confidence · method · logged", "right")]),
        Beat("12", report, "Beat 12 — The Colophon",
             "<strong>Footer names every source CSV, the build command, the canonical schema definition.</strong> The chart-of-accounts is in version control; the rollup carries the schema version it was built against.",
             scroll_to=".colophon, footer",
             actions=[lambda p: _spotlight(p, ".colophon, footer", True)]),
        Beat("13", catalogue, "Beat 13 — Who Picks It Up Next",
             "<strong>The ops associate verifies. The operating partner reads the anomaly section.</strong> The portfolio analytics seat consumes the unified CSV. Three seats, one rollup, one source of truth.",
             scroll_to=card,
             actions=[lambda p: _spotlight(p, card, True),
                      lambda p: _callout(p, card, "associate · ops partner · analytics", "right")]),
        Beat("14", catalogue, "Beat 14 — Outro · Open Source · MIT",
             "<strong>Tool VIII of twelve.</strong> The pre-step every cross-portco analysis needs and almost none of them do.",
             scroll_to=".ticker", actions=[]),
    ]


# ----------------------------------------------------------------------------
# Tool IX — 100-Day Plan Drift Monitor — 14 beats
# ----------------------------------------------------------------------------

def _tool_plan_drift_beats() -> list[Beat]:
    catalogue = _abs("/app/tools.html")
    report = _abs("/finance_output/plan_drift_SoteraCo.html")
    card = "article:nth-of-type(9)"
    return [
        Beat("01", catalogue, "IX · 100-Day Plan Drift Monitor · Beat 1 — Cold Open",
             "<strong>For the operating partner at Day-Sixty.</strong> The MD on the deal team who signed the 100-day plan at close. The CEO at the portco who knows three initiatives are slipping but not the dollar.",
             scroll_to=card,
             actions=[lambda p: _spotlight(p, card, True),
                      lambda p: _callout(p, f"{card} form", "portco ID · ticker · diffed at Day-60", "right")]),
        Beat("02", report, "Beat 2 — The Headline · underline this",
             "<strong>5 of 7 initiatives off-track. –$121.6M of EBITDA at risk against the plan signed at close.</strong> Two months in, with seven left to recover it.",
             scroll_to="h1",
             actions=[lambda p: _bloom(p, "h1")]),
        Beat("03", report, "Beat 3 — Why This Exists",
             "<strong>Plan drift gets surfaced today by the consultant on QBR Monday morning.</strong> By then it's been drifting for six weeks. This catches it at Day-Sixty — diffed against real public 10-Q actuals from SEC EDGAR.",
             scroll_to=".lede, h1",
             actions=[lambda p: _spotlight(p, ".lede, h1", True),
                      lambda p: _callout(p, ".lede, h1", "Day-60 · vs. QBR Monday · vs. consultant", "right")]),
        Beat("04", report, "Beat 4 — The Numbers Under the Headline",
             "<strong>Initiative count · on-track · lagging · off-track · total dollar gap.</strong> An operating partner reads this in fifteen seconds and knows which conversation to have first.",
             scroll_to=".stats-strip",
             actions=[lambda p: _spotlight(p, ".stats-strip", True)]),
        Beat("05", report, "Beat 5 — The Contract",
             "<strong>Direction-aware gap math:</strong> a higher-better KPI and a lower-better KPI both compare correctly against plan. No mis-signed deltas. Status bands at ±5% and ±15%.",
             scroll_to=".stats-strip",
             actions=[lambda p: _spotlight(p, ".stats-strip", True),
                      lambda p: _callout(p, ".stats-strip", "direction-aware · ±5% / ±15%", "right")]),
        Beat("06", report, "Beat 6 — The First Section · Drift Band",
             "<strong>Each initiative laid out by due-day.</strong> Color band tells the status — green on-track, orange lagging, red off-track. The plan was never visible like this in the Word doc the deal team signed.",
             scroll_to=".gantt",
             actions=[lambda p: _spotlight(p, ".gantt", True)]),
        Beat("07", report, "Beat 7 — One Drift Drilled · The Call",
             "<strong>The dynamic-pricing pilot — $85M of recoverable EBITDA, all in one initiative.</strong> Supposed to land in 50 priority centers by Day 80. Currently at zero. The operating partner is calling the CRO before lunch.",
             scroll_to=".gantt-bar.off-track",
             actions=[lambda p: _spotlight(p, ".gantt-bar.off-track, .gantt-row", True),
                      lambda p: _callout(p, ".gantt-bar.off-track, .gantt-row", "$85M · before lunch · the call", "right")]),
        Beat("08", report, "Beat 8 — What a Partner Does With It",
             "<strong>Read the schedule. Mark the top three drifts. Send the rest to the operating analyst.</strong> The schedule becomes the QBR exhibit; the top three become the partner's calls.",
             scroll_to=".gantt",
             actions=[lambda p: _spotlight(p, ".gantt", True),
                      lambda p: _callout(p, ".gantt", "schedule · 3 calls · QBR-ready", "left")]),
        Beat("09", report, "Beat 9 — The Next Section · Initiative Ledger",
             "<strong>Per-initiative ledger.</strong> KPI · target · plan vs. actual · dollar gap · source 10-Q line item. Direction-aware on every row. The portco CFO can verify each plan-vs-actual against their own books in fifteen seconds.",
             scroll_to=".ledger",
             actions=[lambda p: _spotlight(p, ".ledger", True)]),
        Beat("10", report, "Beat 10 — A Second View · Operator Memo",
             "<strong>Operator memo at the bottom — the why, the evidence, the recommended next 30 days.</strong> Same prose contract as the explainer. Every dollar traces to a 10-Q row.",
             scroll_to=".memo",
             actions=[lambda p: _spotlight(p, ".memo", True),
                      lambda p: _callout(p, ".memo", "next 30 days · evidence-grounded", "right")]),
        Beat("11", report, "Beat 11 — Reproducibility",
             "<strong>Reuses the SEC EDGAR fetcher.</strong> Real public 10-Q actuals. Same ticker plus same plan rebuilds the same drift report — without the consultant who built the original plan in the room.",
             scroll_to=".colophon, footer",
             actions=[lambda p: _spotlight(p, ".colophon, footer", True),
                      lambda p: _callout(p, ".colophon, footer", "EDGAR · public · 15s rebuild", "right")]),
        Beat("12", report, "Beat 12 — The Colophon",
             "<strong>Footer names the source ticker, the EDGAR endpoint, the plan definition path.</strong> The plan is in version control; the drift report carries the plan revision it was diffed against.",
             scroll_to=".colophon, footer",
             actions=[lambda p: _spotlight(p, ".colophon, footer", True)]),
        Beat("13", catalogue, "Beat 13 — Who Picks It Up Next",
             "<strong>The operating partner walks in with the page.</strong> The deal MD reads the dollar gap. The portco CEO reads the ledger; the CRO reads the top drift. Four seats, one page.",
             scroll_to=card,
             actions=[lambda p: _spotlight(p, card, True),
                      lambda p: _callout(p, card, "ops partner · MD · CEO · CRO", "right")]),
        Beat("14", catalogue, "Beat 14 — Outro · Open Source · MIT",
             "<strong>Tool IX of twelve.</strong> The page the operating partner walks into the QBR with. The page the consultant didn't ship.",
             scroll_to=".ticker", actions=[]),
    ]


# ----------------------------------------------------------------------------
# Tool X — Procurement Benchmarking — 14 beats
# ----------------------------------------------------------------------------

def _tool_procurement_beats() -> list[Beat]:
    catalogue = _abs("/app/tools.html")
    report = _abs("/finance_output/benchmark_D310_FY2024.html")
    card = "article:nth-of-type(10)"
    return [
        Beat("01", catalogue, "X · Procurement Benchmarking · Beat 1 — Cold Open",
             "<strong>For the operating partner at a mid-market fund without a 50-person procurement team.</strong> The portfolio CFO whose vendor spend is bigger than their EBITDA line. The deal partner asked: <em>can we buy this and run Apollo's playbook?</em>",
             scroll_to=card,
             actions=[lambda p: _spotlight(p, card, True),
                      lambda p: _callout(p, f"{card} form", "PSC code · fiscal year · public", "right")]),
        Beat("02", report, "Beat 2 — The Headline · underline this",
             "<strong>28 buyers · $936M of cross-buyer price spread.</strong> Every dollar traces to two named buyers paying two different prices for the same vendor. Renegotiation opportunity, not a model claim.",
             scroll_to="h1",
             actions=[lambda p: _bloom(p, "h1")]),
        Beat("03", report, "Beat 3 — Why This Exists",
             "<strong>Apollo runs this analysis with 50 procurement analysts on staff.</strong> Mid-market funds can't justify the headcount. The script runs in 10 seconds, surfaces the same spread, walks back to the same line item.",
             scroll_to=".lede, h1",
             actions=[lambda p: _spotlight(p, ".lede, h1", True),
                      lambda p: _callout(p, ".lede, h1", "Apollo's 50-person team · in 10 seconds", "right")]),
        Beat("04", report, "Beat 4 — The Numbers Under the Headline",
             "<strong>Contracts · buyers · vendors · savings opportunity.</strong> The savings column is what a portco's CFO can actually capture — not a forecast, not a vendor claim, the price gap to the best-priced peer.",
             scroll_to=".stats-strip",
             actions=[lambda p: _spotlight(p, ".stats-strip", True)]),
        Beat("05", report, "Beat 5 — The Contract",
             "<strong>Each awarding agency treated as a portco. Each recipient as a vendor.</strong> Same SKU, multiple buyers, the spread is the renegotiation opportunity. Pure stdlib + pandas. The math is comparison.",
             scroll_to=".stats-strip",
             actions=[lambda p: _spotlight(p, ".stats-strip", True),
                      lambda p: _callout(p, ".stats-strip", "agency = portco · recipient = vendor", "right")]),
        Beat("06", report, "Beat 6 — The First Section · Top-Buyer Ledger",
             "<strong>Top buyers ranked by recoverable spend.</strong> State Department leads at $526M. The same vendor is 6.5× higher than DHS pays for the same scope of work.",
             scroll_to=".ledger",
             actions=[lambda p: _spotlight(p, ".ledger", True)]),
        Beat("07", report, "Beat 7 — One Row Drilled · State Department",
             "<strong>Two State Department awards. $625M of cohort spend. $312M per award.</strong> The DHS equivalent — $49M per award. That delta is real, the contracts are public, and the renegotiation conversation walks itself.",
             scroll_to=".rank-1, .ledger",
             actions=[lambda p: _spotlight(p, ".rank-1, .ledger", True),
                      lambda p: _callout(p, ".rank-1, .ledger", "$312M vs. $49M · same vendor · 6.5× spread", "right")]),
        Beat("08", report, "Beat 8 — What a Partner Does With It",
             "<strong>The portco's procurement seat reads the page. The CFO marks the top three contracts for renegotiation.</strong> The deal partner reads the aggregate as a thesis input — <em>can we buy this and run the playbook?</em> Yes, when the spread is this wide.",
             scroll_to=".ledger",
             actions=[lambda p: _spotlight(p, ".ledger", True),
                      lambda p: _callout(p, ".ledger", "procurement · CFO · deal partner · thesis", "left")]),
        Beat("09", report, "Beat 9 — The Next Section · Vendor Spread",
             "<strong>Same vendor across multiple buyers, prices side by side.</strong> The contracts to renegotiate first are the ones with the widest spread, not the ones with the highest absolute spend.",
             scroll_to=".ledger",
             actions=[lambda p: _spotlight(p, ".ledger", True)]),
        Beat("10", report, "Beat 10 — A Second View · One Vendor Drilled",
             "<strong>Top vendor — same service code, same fiscal year, prices ranging 6.5× across buyers.</strong> The procurement team opens the renegotiation with the cheapest price as the floor.",
             scroll_to=".ledger",
             actions=[lambda p: _spotlight(p, ".ledger", True),
                      lambda p: _callout(p, ".ledger", "cheapest as floor · savings → EBITDA", "right")]),
        Beat("11", report, "Beat 11 — Reproducibility",
             "<strong>Same PSC code and same fiscal year in, same spread out.</strong> Public data, public methodology, no auth. The portco can run the analysis on its own contract data — same script, different input.",
             scroll_to=".colophon, footer",
             actions=[lambda p: _spotlight(p, ".colophon, footer", True),
                      lambda p: _callout(p, ".colophon, footer", "USAspending · public · no auth", "right")]),
        Beat("12", report, "Beat 12 — The Colophon",
             "<strong>Footer names the USAspending.gov endpoint, the PSC code, the fiscal year.</strong> The portco's procurement team rebuilds the report on its own data with one arg change.",
             scroll_to=".colophon, footer",
             actions=[lambda p: _spotlight(p, ".colophon, footer", True)]),
        Beat("13", catalogue, "Beat 13 — Who Picks It Up Next",
             "<strong>The portco procurement seat picks it up first.</strong> The CFO sets the renegotiation priorities. The operating partner verifies dollar capture quarter-over-quarter.",
             scroll_to=card,
             actions=[lambda p: _spotlight(p, card, True),
                      lambda p: _callout(p, card, "procurement · CFO · ops partner · deal partner", "right")]),
        Beat("14", catalogue, "Beat 14 — Outro · Open Source · MIT",
             "<strong>Tool X of twelve.</strong> Same data, same math, a hundredth of the headcount. The mid-market fund finally has Apollo's playbook.",
             scroll_to=".ticker", actions=[]),
    ]


# ----------------------------------------------------------------------------
# Tool XI — EU AI Act Compliance Pack — 14 beats
# ----------------------------------------------------------------------------

def _tool_ai_act_beats() -> list[Beat]:
    catalogue = _abs("/app/tools.html")
    report = _abs("/finance_output/ai_act_audit_LendingCo-EU.html")
    card = "article:nth-of-type(11)"
    return [
        Beat("01", catalogue, "XI · EU AI Act Compliance Pack · Beat 1 — Cold Open",
             "<strong>For the General Counsel at the fund.</strong> The compliance seat at a portco classified high-risk under Annex III. The ops partner with EU portcos staring down 2 August 2026.",
             scroll_to=card,
             actions=[lambda p: _spotlight(p, card, True),
                      lambda p: _callout(p, f"{card} form", "portco · use case · system desc", "right")]),
        Beat("02", report, "Beat 2 — The Verdict · underline this",
             "<strong>High-risk per Annex III §5(b) — access to essential private and public services.</strong> 8 articles addressed end-to-end. No obligation invented in prose — every line cites a public regulation.",
             scroll_to=".verdict, h1",
             actions=[lambda p: _bloom(p, ".verdict-tier, .verdict, h1")]),
        Beat("03", report, "Beat 3 — Why This Exists",
             "<strong>No PE-specific product exists for this.</strong> Generic GRC vendors don't understand the diligence cycle. Law-firm memos don't ship a working document. This pack is the skeleton the GC's red-pen pass starts on.",
             scroll_to=".lede, h1",
             actions=[lambda p: _spotlight(p, ".lede, h1", True),
                      lambda p: _callout(p, ".lede, h1", "vs. generic GRC · vs. law-firm memo", "right")]),
        Beat("04", report, "Beat 4 — The Numbers Under the Headline",
             "<strong>The deadline · the use case · the article count.</strong> Three numbers the GC quotes verbatim to the IC. The deadline is moving — every quarter that passes is one fewer to assemble the deliverables.",
             scroll_to=".deadline-strip",
             actions=[lambda p: _spotlight(p, ".deadline-strip", True)]),
        Beat("05", report, "Beat 5 — The Contract",
             "<strong>Classification logic frozen in a Python module — verifiable against the public regulation text.</strong> Every article maps to a deliverable. Every deliverable cites the article paragraph that requires it.",
             scroll_to=".verdict",
             actions=[lambda p: _spotlight(p, ".verdict", True),
                      lambda p: _callout(p, ".verdict", "frozen module · cites EUR-Lex", "right")]),
        Beat("06", report, "Beat 6 — The First Section · Article-by-Article",
             "<strong>Risk management · data governance · technical documentation · record-keeping · transparency · human oversight · accuracy · robustness.</strong> Eight articles. Each names a deliverable.",
             scroll_to=".article-section",
             actions=[lambda p: _spotlight(p, ".article-section", True)]),
        Beat("07", report, "Beat 7 — One Article Drilled · Article 10",
             "<strong>Article 10 — data governance.</strong> Deliverable: training data provenance, bias-detection methodology, mitigation log. The portco has these — they were never assembled into one document. This pack is that document.",
             scroll_to=".article-section",
             actions=[lambda p: _spotlight(p, ".article-section, .deliverables", True),
                      lambda p: _callout(p, ".article-section, .deliverables", "skeleton · GC red-pens · ships", "right")]),
        Beat("08", report, "Beat 8 — What a Partner Does With It",
             "<strong>The GC red-pens the skeleton.</strong> The portco compliance team fills the draft fields. The fund's AI committee signs off. The pack sits on file before the deadline.",
             scroll_to=".article-section",
             actions=[lambda p: _spotlight(p, ".article-section", True),
                      lambda p: _callout(p, ".article-section", "GC · compliance · AI cmte · file", "left")]),
        Beat("09", report, "Beat 9 — The Next Section · Deadline Urgency",
             "<strong>2 August 2026. The deadline isn't moving.</strong> The fund that gets here in March files clean. The fund that gets here in July files in panic. The pack assumes 6 weeks of red-pen, not 6 days.",
             scroll_to=".deadline-strip",
             actions=[lambda p: _spotlight(p, ".deadline-strip", True),
                      lambda p: _callout(p, ".deadline-strip", "2 Aug 2026 · 6 weeks of red-pen", "right")]),
        Beat("10", report, "Beat 10 — A Second View · Limited-Risk Scenario",
             "<strong>A second scenario surfaces correctly as limited-risk.</strong> Marketing personalization is not in Annex III, so the verdict is Article 50 transparency only. The classifier doesn't over-claim.",
             scroll_to=".verdict",
             actions=[lambda p: _spotlight(p, ".verdict", True),
                      lambda p: _callout(p, ".verdict", "doesn't over-claim · scoped correctly", "right")]),
        Beat("11", report, "Beat 11 — Reproducibility",
             "<strong>Same use-case category in, same verdict out.</strong> Frozen Annex III. Verifiable against EUR-Lex. Air-gappable — runs without internet. The classification logic is auditable line-by-line.",
             scroll_to=".colophon, footer",
             actions=[lambda p: _spotlight(p, ".colophon, footer", True),
                      lambda p: _callout(p, ".colophon, footer", "EUR-Lex · air-gappable · line-auditable", "right")]),
        Beat("12", report, "Beat 12 — The Colophon",
             "<strong>Footer names the regulation citation, the classification module, the build command.</strong> The regulation reference is permanent; the module sits in version control. Three years from now, the audit walks back to the same commit.",
             scroll_to=".colophon, footer",
             actions=[lambda p: _spotlight(p, ".colophon, footer", True)]),
        Beat("13", catalogue, "Beat 13 — Who Picks It Up Next",
             "<strong>The GC red-pens.</strong> The compliance seat assembles the deliverables. The portco CTO populates technical documentation. The ops partner tracks deadline progress.",
             scroll_to=card,
             actions=[lambda p: _spotlight(p, card, True),
                      lambda p: _callout(p, card, "GC · compliance · CTO · ops partner", "right")]),
        Beat("14", catalogue, "Beat 14 — Outro · Open Source · MIT",
             "<strong>Tool XI of twelve.</strong> The skeleton, drafted in 60 seconds, sized for the GC's red-pen pass.",
             scroll_to=".ticker", actions=[]),
    ]


# ----------------------------------------------------------------------------
# Tool XII — Agent Sprawl Auditor — 14 beats
# ----------------------------------------------------------------------------

def _tool_agent_sprawl_beats() -> list[Beat]:
    catalogue = _abs("/app/tools.html")
    report = _abs("/finance_output/audit_agents_server.html")
    card = "article:nth-of-type(12)"
    return [
        Beat("01", catalogue, "XII · Agent Sprawl Auditor · Beat 1 — Cold Open",
             "<strong>For the CFO at the fund staring at the Anthropic / OpenAI bill.</strong> The CTO at a portco whose half-shipped agent prototypes are still spending money in production.",
             scroll_to=card,
             actions=[lambda p: _spotlight(p, card, True),
                      lambda p: _callout(p, f"{card} form", "no inputs · runs on the registry", "right")]),
        Beat("02", report, "Beat 2 — The Headline · underline this",
             "<strong>1 healthy · 14 zombies · 2 misaligned · 1 runaway.</strong> Zombies ran last quarter, billed last quarter, and shipped no artifact since. The runaway is the one billing every day.",
             scroll_to="h1",
             actions=[lambda p: _bloom(p, "h1")]),
        Beat("03", report, "Beat 3 — Why This Exists",
             "<strong>Vista, Thoma Bravo, and other mega-funds are deploying AI agents at scale.</strong> Gartner forecasts 40% of agentic projects cancelled by 2027. Without an audit, ghost agents burn budget for months.",
             scroll_to=".lede, h1",
             actions=[lambda p: _spotlight(p, ".lede, h1", True),
                      lambda p: _callout(p, ".lede, h1", "Gartner · 40% cancelled · 2027", "right")]),
        Beat("04", report, "Beat 4 — The Numbers Under the Headline",
             "<strong>Total agents · healthy · flagged · annual savings if pruned.</strong> The savings column is what the CFO files. The flagged column is what the CTO acts on.",
             scroll_to=".stats-strip",
             actions=[lambda p: _spotlight(p, ".stats-strip", True)]),
        Beat("05", report, "Beat 5 — The Contract",
             "<strong>Inventory comes from AST-walking the server registration file.</strong> Every <code>mcp.add_tool</code> call becomes an inventory row. Real registry, modeled telemetry. Pricing from Anthropic's published list.",
             scroll_to=".stats-strip",
             actions=[lambda p: _spotlight(p, ".stats-strip", True),
                      lambda p: _callout(p, ".stats-strip", "AST walk · public pricing · audit-cited", "right")]),
        Beat("06", report, "Beat 6 — The First Section · Health Verdicts",
             "<strong>Three deterministic checks.</strong> Zombie: hasn't run in 30 days. Misaligned: produced an artifact whose eval failed. Runaway: ran more than 2× the baseline rate.",
             scroll_to=".pill, .flagged",
             actions=[lambda p: _spotlight(p, ".pill.zombie, .pill, .flagged", True)]),
        Beat("07", report, "Beat 7 — One Agent Drilled · CIM Extractor",
             "<strong>The CIM extractor.</strong> Sonnet at $1,080/run. 88 runs since deployment. Triple-flagged: zombie · runaway · misaligned. Top of the prune list at $13K/year.",
             scroll_to=".pill.runaway, .flagged",
             actions=[lambda p: _spotlight(p, ".pill.runaway, .pill.misaligned, .flagged", True),
                      lambda p: _callout(p, ".pill.runaway, .pill.misaligned, .flagged", "$1,080/run · triple-flagged · prune", "right")]),
        Beat("08", report, "Beat 8 — What a Partner Does With It",
             "<strong>The CFO reads the savings column. The CTO walks the flagged rows. The ops partner files the audit alongside the quarterly review.</strong> The fund's AI-cost line goes from a black box to a one-page audit.",
             scroll_to=".prune-list, .flagged",
             actions=[lambda p: _spotlight(p, ".prune-list, .flagged", True),
                      lambda p: _callout(p, ".prune-list, .flagged", "CFO · CTO · ops · quarterly cleanup", "left")]),
        Beat("09", report, "Beat 9 — The Next Section · Pruning Recommendations",
             "<strong>Pruning list ordered by annual savings.</strong> 16 agents flagged · $19,880 total annual savings if all pruned. Modeled, named, defensible — not a vendor complaint.",
             scroll_to=".prune-list",
             actions=[lambda p: _spotlight(p, ".prune-list", True)]),
        Beat("10", report, "Beat 10 — A Second View · Cost Attribution",
             "<strong>Cost-per-agent, named explicitly.</strong> Sonnet-4-6 at $360 for explainer. $1,080 for CIM extractor. Haiku-4-5 at $17.50 for DX stages. The CFO finally has the line item, by tool.",
             scroll_to=".savings, .num",
             actions=[lambda p: _spotlight(p, ".prune-list, .savings, .num", True),
                      lambda p: _callout(p, ".prune-list, .savings, .num", "by tool · by model · by run", "right")]),
        Beat("11", report, "Beat 11 — Reproducibility",
             "<strong>AST walk + pandas + deterministic synthetic clock. No LLM at runtime.</strong> Run twice on the same registry — identical output. Replace synthetic telemetry with production via a hook; the audit logic doesn't change.",
             scroll_to=".caveat, .colophon, footer",
             actions=[lambda p: _spotlight(p, ".caveat, .colophon, footer", True),
                      lambda p: _callout(p, ".caveat, .colophon, footer", "deterministic · model-free · same in, same out", "right")]),
        Beat("12", report, "Beat 12 — The Colophon",
             "<strong>Footer names the registry path, the pricing source, the build command.</strong> The pricing source is Anthropic's public pricing page — documented, citable, audit-defensible.",
             scroll_to=".colophon, footer",
             actions=[lambda p: _spotlight(p, ".colophon, footer", True)]),
        Beat("13", catalogue, "Beat 13 — Who Picks It Up Next",
             "<strong>The CFO files the savings.</strong> The CTO prunes the flagged agents. The ops partner reviews quarterly. The portco's own audit picks up the same template.",
             scroll_to=card,
             actions=[lambda p: _spotlight(p, card, True),
                      lambda p: _callout(p, card, "CFO · CTO · ops · portco audit", "right")]),
        Beat("14", catalogue, "Beat 14 — Outro · Open Source · MIT",
             "<strong>Tool XII of twelve.</strong> Every agent on a fund's roster needs cost attribution, success rate, and a renewal decision. This makes that audit one command long.",
             scroll_to=".ticker", actions=[]),
    ]


TOOLS = {
    "01-dx":          ("Tool I — Decision Diagnostic", _tool_dx_beats),
    "02-explainer":   ("Tool II — Model-to-Narrative Explainer", _tool_explainer_beats),
    "03-bx":          ("Tool III — Cross-Portco Benchmarking (BX)", _tool_bx_beats),
    "04-eval":        ("Tool IV — LLM Eval for PE", _tool_eval_beats),
    "05-cim":         ("Tool V — CIM Red-Flag Extractor", _tool_cim_beats),
    "06-seller-pack": ("Tool VI — Seller-Side AI Diligence Pack", _tool_seller_pack_beats),
    "07-ddq":         ("Tool VII — DDQ Automation + Consistency", _tool_ddq_beats),
    "08-normalize":   ("Tool VIII — Portfolio Normalization", _tool_normalize_beats),
    "09-plan-drift":  ("Tool IX — 100-Day Plan Drift Monitor", _tool_plan_drift_beats),
    "10-procurement": ("Tool X — Procurement Benchmarking", _tool_procurement_beats),
    "11-ai-act":      ("Tool XI — EU AI Act Compliance Pack", _tool_ai_act_beats),
    "12-agent-sprawl":("Tool XII — Agent Sprawl Auditor", _tool_agent_sprawl_beats),
}


def render_tool(tool_id: str) -> Path:
    if tool_id not in TOOLS:
        raise SystemExit(f"unknown tool: {tool_id} (try: {', '.join(TOOLS)})")

    label, beats_fn = TOOLS[tool_id]
    beats = beats_fn()
    out_dir = OUT_ROOT / tool_id
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[render] {label} — {len(beats)} beats → {out_dir}/")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport=VIEWPORT, device_scale_factor=2)
        page = context.new_page()
        last_url: str | None = None
        for beat in beats:
            if beat.url != last_url:
                page.goto(beat.url, wait_until="networkidle")
                last_url = beat.url
                # Give Google Fonts a beat to load before screenshot.
                time.sleep(0.6)

            _install_overlay(page)
            _clear(page)
            if beat.scroll_to:
                _scroll(page, beat.scroll_to)
                time.sleep(0.2)
            for action in beat.actions:
                try:
                    action(page)
                except Exception as exc:
                    print(f"  ! beat {beat.n} action error: {exc}")
            _caption(page, beat.kicker, beat.line)
            time.sleep(0.4)
            out_path = out_dir / f"{beat.n}.png"
            page.screenshot(path=str(out_path), full_page=False)
            print(f"  · {beat.n}.png  ({out_path.stat().st_size // 1024} KB)")

        context.close()
        browser.close()
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tool",
        choices=list(TOOLS.keys()) + ["all"],
        default="01-dx",
    )
    args = parser.parse_args()
    targets = list(TOOLS.keys()) if args.tool == "all" else [args.tool]
    for t in targets:
        render_tool(t)
    print(f"\nFrames written under: {OUT_ROOT}/")


if __name__ == "__main__":
    sys.exit(main())
