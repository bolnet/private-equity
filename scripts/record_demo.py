"""
Record a partner-grade explainer video for any tool in the PE × AI
catalogue. Each profile pairs an audience callout (which seat in the
PE org chart watches this) with on-screen captions and the underline-
this beats from .local/demo/voiceover-scripts.md.

Profiles cover all 12 tools:

    DX (upload-driven · Tool I · requires the local web server):
        python scripts/record_demo.py --profile lending
        python scripts/record_demo.py --profile yasserh
        python scripts/record_demo.py --profile hmda

    BX cross-portco family (navigation over rendered HTML · no server):
        python scripts/record_demo.py --profile bx-hmda-states  # Tool III
        python scripts/record_demo.py --profile bx-mixed-fund   # Tool III
        python scripts/record_demo.py --profile bx-procurement  # Tool X
        python scripts/record_demo.py --profile bx-plan-drift   # Tool IX

    Tool walkthroughs (navigation over rendered HTML · no server):
        python scripts/record_demo.py --profile tool-explainer    # II
        python scripts/record_demo.py --profile tool-eval         # IV
        python scripts/record_demo.py --profile tool-cim          # V
        python scripts/record_demo.py --profile tool-seller-pack  # VI
        python scripts/record_demo.py --profile tool-ddq          # VII
        python scripts/record_demo.py --profile tool-normalize    # VIII
        python scripts/record_demo.py --profile tool-eu-ai-act    # XI
        python scripts/record_demo.py --profile tool-agent-sprawl # XII

DX needs the server running first:
    python -m finance_mcp.web 8765   # or:  pe-mcp-web

BX and tool-* profiles read finance_output/*.html via file://, no server.

    # Output: /tmp/pe-demo/<hash>.webm

Convert to MP4 (one line printed at the end of the run):
    ffmpeg -y -i /tmp/pe-demo/<hash>.webm \
           -c:v libx264 -crf 20 -preset slow \
           /tmp/pe-demo/<hash>.mp4

Pair every recording with the matching voiceover scene in
.local/demo/voiceover-scripts.md — the captions carry the headline,
the voice carries the *why*.
"""
from __future__ import annotations

import argparse
import base64
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Page, sync_playwright


_CAPTION_JS = r"""
(() => {
  if (document.getElementById('__demo_caption_root')) return;
  const root = document.createElement('div');
  root.id = '__demo_caption_root';
  root.innerHTML = `
    <style>
      #__demo_caption_root {
        position: fixed; left: 50%; bottom: 36px; transform: translateX(-50%);
        z-index: 999999; max-width: 860px; width: calc(100% - 48px);
        pointer-events: none; font-family: 'Inter', -apple-system, sans-serif;
      }
      #__demo_caption_box {
        background: rgba(10, 12, 16, 0.94);
        border: 1px solid rgba(245, 215, 110, 0.4);
        border-radius: 14px;
        padding: 14px 22px;
        color: #e6e9ef;
        box-shadow: 0 16px 48px rgba(0,0,0,0.45), 0 0 0 1px rgba(245,215,110,0.08);
        backdrop-filter: blur(14px);
        opacity: 0; transform: translateY(18px);
        transition: opacity .42s ease, transform .42s ease;
      }
      #__demo_caption_box.show { opacity: 1; transform: translateY(0); }
      #__demo_caption_kicker {
        font-family: 'JetBrains Mono', monospace;
        font-size: 10.5px; letter-spacing: 0.18em; text-transform: uppercase;
        color: #f5d76e; margin-bottom: 6px;
      }
      #__demo_caption_text {
        font-size: 17px; line-height: 1.45; color: #e6e9ef; font-weight: 500;
      }
      #__demo_caption_hint {
        font-size: 13.5px; color: #9aa3b2; margin-top: 6px; line-height: 1.45;
      }
    </style>
    <div id="__demo_caption_box">
      <div id="__demo_caption_kicker"></div>
      <div id="__demo_caption_text"></div>
      <div id="__demo_caption_hint"></div>
    </div>
  `;
  document.body.appendChild(root);
  window.__setCaption = (kicker, text, hint) => {
    const box = document.getElementById('__demo_caption_box');
    box.classList.remove('show');
    setTimeout(() => {
      document.getElementById('__demo_caption_kicker').textContent = kicker || '';
      document.getElementById('__demo_caption_text').textContent = text || '';
      document.getElementById('__demo_caption_hint').textContent = hint || '';
      requestAnimationFrame(() => box.classList.add('show'));
    }, 180);
  };
  window.__clearCaption = () => {
    const box = document.getElementById('__demo_caption_box');
    if (box) box.classList.remove('show');
  };
})();
"""


def set_caption(page: Page, kicker: str, text: str, hint: str = "") -> None:
    """Show a lower-third caption overlay. Call set_caption('', '', '') to clear."""
    page.evaluate(_CAPTION_JS)  # idempotent
    if not (kicker or text or hint):
        page.evaluate("window.__clearCaption && window.__clearCaption()")
    else:
        page.evaluate(
            "([k,t,h]) => window.__setCaption(k,t,h)",
            [kicker, text, hint],
        )


def drop_files_visibly(page: Page, zone_selector: str, file_paths: list[Path]) -> None:
    """
    Simulate a user dragging files onto a drop zone — unlike set_input_files,
    this fires dragenter/dragover/drop DOM events, which triggers the drop
    zone's hover styling and ondrop handler so the animation is visible on
    camera.
    """
    payload = []
    for path in file_paths:
        payload.append({
            "name": path.name,
            "mime": "text/csv",
            "b64": base64.b64encode(path.read_bytes()).decode(),
        })

    page.evaluate(
        """async ({ files, selector }) => {
            const decoded = files.map(fd => {
                const bin = atob(fd.b64);
                const bytes = new Uint8Array(bin.length);
                for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
                return new File([bytes], fd.name, { type: fd.mime });
            });

            const dt = new DataTransfer();
            decoded.forEach(f => dt.items.add(f));
            const zone = document.querySelector(selector);

            zone.dispatchEvent(new DragEvent('dragenter', { bubbles: true, dataTransfer: dt }));
            zone.dispatchEvent(new DragEvent('dragover',  { bubbles: true, dataTransfer: dt }));
            await new Promise(r => setTimeout(r, 900));
            zone.dispatchEvent(new DragEvent('drop',      { bubbles: true, dataTransfer: dt }));
        }""",
        {"files": payload, "selector": zone_selector},
    )


URL_APP = "http://127.0.0.1:8765/app/"
REPO = Path(__file__).resolve().parent.parent
OUT = Path("/tmp/pe-demo")
OUT.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Profile:
    """A DX upload-flow profile — input CSVs + captions for the 8 scenes."""
    name: str
    portco_id: str
    loans: Path
    perf: Path
    regen_hint: str
    intro_kicker: str
    intro_text: str
    intro_hint: str
    entities_text: str
    entities_hint: str
    finding_text: str
    finding_hint: str
    opp_text: str
    opp_hint: str
    mode: str = "dx"  # "dx" | "bx"


@dataclass(frozen=True)
class BXProfile:
    """A BX walkthrough profile — points at a rendered fund-level HTML report."""
    name: str
    report_path: Path
    regen_hint: str
    intro_kicker: str
    intro_text: str
    intro_hint: str
    archetype_text: str
    archetype_hint: str
    rank_text: str
    rank_hint: str
    top_portco_text: str
    top_portco_hint: str
    peer_text: str
    peer_hint: str
    outro_text: str
    outro_hint: str
    mode: str = "bx"
    # Optional pre-tour fields — when set, the video opens at the
    # catalogue UI (tools.html), frames the partner pain, highlights
    # this tool's tile, then enters the report walkthrough.
    problem_text: str = ""           # The partner pain this tool addresses (Beat A)
    problem_hint: str = ""           # Subtitle / supporting context for Beat A
    catalogue_match: str = ""        # Substring of <h2 class='entry-name'> to scroll/highlight (Beat C)


def _tool_profile(name, report, regen, kicker, intro, intro_hint, headline, headline_hint, num, num_hint, sect1, sect1_hint, sect2, sect2_hint, outro, outro_hint, *, problem="", problem_hint="", catalogue_match=""):
    return BXProfile(
        name=name, report_path=REPO / "finance_output" / report, regen_hint=regen,
        intro_kicker=kicker, intro_text=intro, intro_hint=intro_hint,
        archetype_text=headline, archetype_hint=headline_hint,
        rank_text=num, rank_hint=num_hint,
        top_portco_text=sect1, top_portco_hint=sect1_hint,
        peer_text=sect2, peer_hint=sect2_hint,
        outro_text=outro, outro_hint=outro_hint,
        mode="report",
        problem_text=problem,
        problem_hint=problem_hint,
        catalogue_match=catalogue_match,
    )


BX_PROFILES: dict[str, BXProfile] = {
    "tool-explainer": _tool_profile(
        "tool-explainer", "explain_MortgageCo_board.html",
        "python -c 'from finance_mcp.explainer import explain_decision; explain_decision(opportunity_map_path=\"finance_output/dx_report_MortgageCo.json\")'",
        "MODEL-TO-NARRATIVE EXPLAINER",  # Pre-tour kwargs at the end of this call.
        "For the deal partner walking into IC — the MD with eight minutes on the agenda and a page-and-a-half to defend a recommendation.",
        "The diagnostic surfaces the dollars. This tool turns the dollars into a memo a partner reads into the record. Two audiences from the same evidence — board memo, operator memo.",
        "Three repeatable opportunities. One-point-one-two billion per annum. Every figure citable.",
        "Real Yasserh mortgage data — 30,000 loans, 24% default rate, $1.1B in losses. The diagnostic surfaces three high-volume decision patterns the operator can act on without a wholesale repricing.",
        "The summary block stays at the top. Headline plus recommendation in two short paragraphs — the language a managing director can read into a board meeting on Wednesday.",
        "No fabricated numbers. Every dollar in this memo traces to an OpportunityMap field; the renderer enforces it programmatically.",
        "Each opportunity gets four sections: the decision, the counterfactual, risk of inaction, rollout plan. Drop cap on the first paragraph, Roman-numeral section markers, marginalia ledger pulled out into a right gutter on wide viewports.",
        "Editorial-letterpress design — Cormorant Garamond display, EB Garamond body, oxblood accent on warm cream paper, paper-grain SVG overlay. It looks like an LP letter, not a SaaS dashboard. Deliberate.",
        "The counterfactual paragraph is the part that survives diligence questioning. It names what changes if the cohort gets rerouted — annual outcome flips from negative to break-even, recovering the modeled impact.",
        "Persistence score across quarters tells you whether a finding is structural or vintage-specific. Anything above zero-point-eight gets called structural in the prose.",
        "Open source · MIT · pandas-deterministic. The wedge into a portco's data; the artifact a partner can put in front of an LP.",
        "github.com/bolnet/private-equity",
        problem=(
            "Today I want to talk about the moment a deal partner stands up at IC "
            "with a recommendation and can't defend the why. The diagnostic told "
            "you the dollar — but the room asks what changed, what the alternative "
            "was, what the risk of doing nothing looks like."
        ),
        problem_hint=(
            "Eight minutes on the agenda. A page-and-a-half. The boardroom where "
            "the recommendation either lands or doesn't."
        ),
        catalogue_match="Model-to-Narrative Explainer",
    ),
    "tool-cim": _tool_profile(
        "tool-cim", "cim_redflags_SHC.html",
        "python -c 'from finance_mcp.cim import cim_analyze; cim_analyze(ticker=\"SHC\", form=\"10-K\")'",
        "CIM RED-FLAG EXTRACTOR",
        "For the diligence VP reading a 500-page CIM the night before IC — and the associate building the red-flag schedule for the partner.",
        "Drop a CIM, an S-1, or any 10-K. Get back a section-cited list of red flags an associate would otherwise spend two nights surfacing by hand. Real Sotera Health 10-K, public via EDGAR.",
        "Forty-nine red flags. Section-cited. One page.",
        "Sixteen high-severity, thirty-two medium, one low. Every flag has a 1-2 sentence excerpt + a section citation. Severity is heuristic — calibrated for diligence-priority recall, not precision.",
        "The eight flag families: customer concentration, going concern, material weakness, goodwill impairment, auditor change, related-party transactions, restatement, and severe-language risk factors. Standard PE diligence checklist, automated.",
        "Heuristics deliberately over-call. A diligence reviewer prefers false positives — cheap to dismiss — over false negatives where the deal closes on a missed flag.",
        "Each flag block carries the source paragraph as an italic pull-quote, plus a 'why it's flagged' rationale. The rationale tells the reviewer what to do next — confirm with auditor, check magnitude, name the entity.",
        "Goodwill impairment is high-severity by default — it indicates a prior acquisition's modeled value didn't materialize. Always worth diligence on the underlying asset.",
        "Material weakness flags appear in both Item 1A and Item 9A. The 9A version is canonical management disclosure; the 1A version is risk-factor reframing. Both surface here.",
        "Severity language scan picks up paragraphs in Item 1A with three or more 'material', 'substantial', 'adverse' hits — the operator's own self-disclosed top concerns, stack-ranked.",
        "Eight extractors, deterministic, no LLM. Run twice on the same filing — identical output. The math is regex; the contract is reproducibility.",
        "github.com/bolnet/private-equity",
        problem=(
            "Picture the diligence VP at midnight. A five-hundred-page CIM in front "
            "of them, IC at nine, and a partner waiting on a defensible red-flag "
            "schedule. Two associate-nights of page-flipping ahead of them. That's "
            "the work this tool replaces — without losing the section citation."
        ),
        problem_hint="Section-cited red flags. Verbatim excerpts. One page that becomes the IC schedule.",
        catalogue_match="CIM Red-Flag Extractor",
    ),
    "tool-seller-pack": _tool_profile(
        "tool-seller-pack", "exit_proof_pack_MortgageCo.html",
        "python -c 'from finance_mcp.seller_pack import exit_proof_pack; exit_proof_pack(portco_id=\"MortgageCo\", opportunity_map_path=\"finance_output/dx_report_MortgageCo.json\")'",
        "SELLER-SIDE AI EBITDA PROOF PACK",
        "For the deal partner at exit, the banker running the sale, and the portco CFO who has to defend the IM's AI-EBITDA number to a buyer who *will* try to take it back.",
        "Buyer diligence will ask: prove the AI EBITDA. This is the trail you give them — before they ask — so the number doesn't get haircut at LOI.",
        "$1.12B base · sensitivity $560M – $1.46B · three documented claims.",
        "Three claims, each tied to a DX OpportunityMap row. Conservative is fifty percent of base; aggressive is one-thirty percent. The buyer's diligence team can re-derive the table from the same source data.",
        "Provenance ledger surfaces every claim with its source artifact and evidence row IDs. A buyer can pull the underlying CSV and spot-check.",
        "No fabrication. Every dollar comes from an OpportunityMap field; the renderer raises if portco_id mismatches the source.",
        "Sensitivity table at fifty / hundred / one-thirty percent multipliers. The multipliers are frozen module-level constants so the math is reproducible across any seller-side run.",
        "Direction-aware: claims that depend on rerouting volume use the conservative end; claims that depend on price changes use the aggressive end. Documented in the methodology block.",
        "Defensibility checklist per claim: would-the-buyer-challenge, has-counterfactual, has-persistence-data, has-row-evidence. Yes-no for each. The seller's banker reads this first.",
        "If a claim has 'no row evidence', the proof pack flags it explicitly. That's the honest disclosure that builds trust with diligence.",
        "Pure whitespace in the market. Apollo, Vista, Thoma Bravo all need this on every exit. Open source · MIT.",
        "github.com/bolnet/private-equity",
        problem=(
            "Here's a scenario. The portco is at exit. The IM claims AI-attributable "
            "EBITDA. The buyer's diligence team will try to take that number back at "
            "LOI. Most sellers walk into that conversation with a slide deck. This "
            "tool gives them an audit trail — provenance ledger, sensitivity range, "
            "defensibility checklist."
        ),
        problem_hint="The pack a seller files on day one — before the buyer asks.",
        catalogue_match="Seller-Side AI Diligence Pack",
    ),
    "tool-ddq": _tool_profile(
        "tool-ddq", "ddq_response_Bolnet_Capital_Partners_I.html",
        "python -c 'from finance_mcp.ddq import ddq_respond; ddq_respond(fund_name=\"Bolnet Capital Partners I\")'",
        "DDQ AUTOMATION + CONSISTENCY",
        "For the IR seat staring down a 40-question ILPA AI DDQ at 11pm — and the CFO at the fund who has to sign the answers.",
        "The Q1 2026 ILPA AI diligence questions, answered against the actual artifacts in this fund's working directory — not against marketing copy. Every answer cites the file it came from.",
        "12 questions answered · 38 artifacts indexed · 26 consistency checks before the LP sees the packet.",
        "Thirty-eight knowledge-base artifacts indexed. Every answer cites the specific JSON sidecar it pulled evidence from. The retrieval is regex-based, deterministic — same archive in, same answers out.",
        "Twelve ILPA-shaped questions across seven categories: governance, data, model risk, vendor, regulatory, valuation, exit. Frozen in a Python module so the question set is auditable.",
        "These aren't the literal ILPA template — that's paywalled — but they're ILPA-shaped questions any LP would ask in 2026. Customizable per fund.",
        "Consistency checker surfaces contradictions: question one says the fund has fourteen portfolio companies, question nine says only two have AI Act exposure. That's the kind of thing an LP catches.",
        "Numeric mismatch is high-severity. Entity-orphan — naming a portco in one answer but not in the inventory — is medium. Both flag the answer that needs human review.",
        "First-draft is the contract. The fund's IR team still reviews and edits. The win is consistency across answers — no two responses say different numbers about the same thing.",
        "Every contradiction surfaces with citation. The IR analyst sees 'Q1 cites 14 portcos · Q9 cites 2 portcos' and knows which to fix.",
        "The first GP with a consistency layer wins the next allocation cycle. Open source · MIT.",
        "github.com/bolnet/private-equity",
        problem=(
            "It's eleven at night. The IR seat has a forty-question ILPA AI DDQ on "
            "the desk and the LP needs answers by Monday. Most funds answer these "
            "by hand, with three people on a call, and the answers contradict each "
            "other across questions. This tool retrieves first-draft answers from "
            "the fund's existing archive and runs a consistency check before the "
            "LP sees the packet."
        ),
        problem_hint="Twelve ILPA questions answered. Twenty-six contradictions surfaced. Before send, not after.",
        catalogue_match="DDQ Automation",
    ),
    "tool-normalize": _tool_profile(
        "tool-normalize", "normalize_3portcos.html",
        "python -c 'from finance_mcp.normalize import normalize_portco; normalize_portco(portco_csv_paths=[\"demo/regional_lenders/midwest_lender/loans.csv\",\"demo/yasserh_mortgages/loans.csv\",\"demo/hmda_states/ga/loans.csv\"], portco_ids=[\"midwest_lender\",\"MortgageCo\",\"HMDA_GA\"])'",
        "PORTFOLIO NORMALIZATION",
        "For the operating partner running portfolio analytics — and the CFO at a portco whose chart of accounts looks nothing like the next portco's.",
        "Three portcos. Three different chart-of-accounts. One unified view, with every cell traceable to its source field. The pre-step every cross-portco analysis needs and almost none of them do.",
        "3 portcos folded · 195,473 rows normalized · 9 anomalies flagged before rollup.",
        "Real data — Lending Club consumer loans (Midwest US), Yasserh US mortgages 2019, CFPB HMDA Georgia 2023. Three different schemas, three different products. Mapped onto the canonical lending_b2c chart of accounts.",
        "Mapping happens in three precedence layers: alias lookup, regex match, fuzzy token-Jaccard. Every match logged with confidence score in the audit JSON.",
        "Target-collision resolver picks the highest-confidence match when two source columns vie for the same canonical field. The losers are dropped, transparently.",
        "Three-axis anomaly detector: magnitude (one portco's median way off the peer extreme), sign-flip (positive in some, negative in others), coverage (canonical field empty in some portcos).",
        "Nine anomalies surfaced. The biggest: Midwest Lending's median loan size of twelve thousand dollars versus Yasserh's two-hundred-ninety-six thousand — a twenty-four-times scale gap that signals consumer versus mortgage product mix, not a unit error.",
        "The gap is real, defensible, surfaced automatically. An operating partner doesn't need to spend three hours staring at columns to know the comparison is apples-to-oranges.",
        "Mapping audit JSON is the audit trail. Open it, see exactly which source column became which canonical field, with confidence score and method. Auditable end-to-end.",
        "Pure pandas plus regex. No external deps. Runs on the operator's laptop. Open source · MIT.",
        "github.com/bolnet/private-equity",
        problem=(
            "The operating partner runs portfolio analytics across N portcos. "
            "Every portco's chart of accounts looks different — different revenue "
            "recognition, different period definitions, different field names for "
            "the same concept. They normalize by hand into one comparable view. "
            "This tool kills the ritual without losing the audit trail."
        ),
        problem_hint="Three different schemas. One unified view. Every cell tracing home to its source field.",
        catalogue_match="Portfolio Normalization",
    ),
    "tool-eval": _tool_profile(
        "tool-eval", "eval_corpus_summary.html",
        "python -m scripts.run_eval_corpus",
        "LLM EVAL FOR PE",
        "For the head of data at the fund. The compliance seat reviewing any AI-generated artifact before it reaches LPs. The IC member who wants to know whether to trust the memo.",
        "Citation accuracy. Hallucination rate. Coverage. Consistency. Four numbers, one rubric, deterministic scoring. The eval is the contract every memo crosses before it reaches an LP.",
        "13 memos scored · 87% citation accuracy · 100% coverage · 100% consistency.",
        "Eighty-seven percent mean citation accuracy across the corpus. Hundred percent coverage and hundred percent consistency. Hallucination rate of thirty-one percent — that's a calibration finding, not noise.",
        "Citation accuracy: every dollar figure in the memo's prose must trace back to a numeric field in the source OpportunityMap. We catch even rounding-error mismatches.",
        "Hallucination rate counts entities — bold-named cohorts, dates, named segments — that appear in the memo but not in the source. Whitelisted playbook copy like 'throttle to ~5%' gets flagged; that's why the rate sits around thirty percent on the deterministic templates.",
        "Coverage: how many of the source's opportunities did the memo actually address? All thirteen memos hit one hundred percent. The renderer enforces this.",
        "Consistency: when the same source produces multiple memos — board and operator audiences — do their headline numbers agree? Yes for every pair tested.",
        "The corpus eval surfaces real outliers. HMDA Delaware and HMDA Massachusetts both showed thirty-three percent citation accuracy — worth investigating. The rubric caught something specific.",
        "This is what engineering discipline looks like in PE × AI. The same harness can be turned on a competitor's output to show what theirs is missing. That's the demo for a fund partner.",
        "Pure pandas plus regex. Deterministic — same memo and source in, same scores out. Open source · MIT.",
        "github.com/bolnet/private-equity",
        problem=(
            "Here's the problem nobody in PE is talking about yet. Your fund ships "
            "AI-generated memos. Your LPs read them. Your IC signs off on them. "
            "And there is no eval, no rubric, no audit trail. This tool is the "
            "QA layer every memo crosses before an LP sees it."
        ),
        problem_hint="Citation accuracy. Coverage. Consistency. Hallucination. Four numbers, one rubric, deterministic scoring.",
        catalogue_match="LLM Eval for PE",
    ),
    "tool-eu-ai-act": _tool_profile(
        "tool-eu-ai-act", "ai_act_audit_LendingCo-EU.html",
        "python -c 'from finance_mcp.eu_ai_act import ai_act_audit; ai_act_audit(portco_id=\"LendingCo-EU\", ai_system_description=\"Consumer credit-decisioning ML model.\", use_case_category=\"credit_decisioning\")'",
        "EU AI ACT COMPLIANCE PACK",
        "For the General Counsel at the fund. The compliance seat at a portco classified high-risk under Annex III. The ops partner with EU portcos staring down 2 August 2026.",
        "Regulation (EU) 2024/1689 — the AI Act. Article 6 high-risk classification. Documentation skeleton, every obligation tracing to a public article, sized for the GC's red-pen pass.",
        "High-risk verdict · Annex III §5(b) · access to essential private services.",
        "Consumer credit-decisioning falls squarely under Annex III's enumerated high-risk categories. The classification logic is frozen in a Python module — verifiable against the public regulation text.",
        "Every Article surfaces a compliance documentation skeleton. Article Six classification verdict, Article Nine risk management system, Article Ten data and data governance, Article Eleven technical documentation, Twelve record-keeping, Thirteen transparency, Fourteen human oversight, Fifteen accuracy and robustness.",
        "Each Article's deliverables are listed in the report with a portco-context-aware skeleton. The portco's CTO doesn't write from a blank page.",
        "The deadline of two-August twenty-twenty-six surfaces on every page footer. Time-bound regulation; hard date; no generic GRC vendor will be PE-shaped by then.",
        "Annex Three has eight enumerated areas: biometric ID, critical infrastructure, education, employment, essential services, law enforcement, migration, justice administration. Most PE high-risk exposure sits in section five — essential services like consumer credit and insurance.",
        "A second scenario surfaces correctly as limited-risk: marketing personalization is not in Annex III, so the verdict is Article Fifty transparency obligations only. The classifier doesn't over-claim.",
        "The pack is meant to be the first thing a portco's CTO and legal counsel sit down with. It identifies the gap, surfaces the deliverables, anchors to the public regulation.",
        "Frozen Annex Three. Verifiable against EUR-Lex. Air-gappable — runs without internet. Open source · MIT.",
        "github.com/bolnet/private-equity",
        problem=(
            "Two August 2026. That's the deadline for high-risk AI classification "
            "under the EU AI Act. If your fund has EU LPs, EU portcos, or EU data "
            "flows, you need Article-6-through-15 documentation by then. No "
            "PE-specific product exists for this. Generic GRC vendors don't "
            "understand the diligence cycle. Law-firm memos don't ship a working "
            "document."
        ),
        problem_hint="Eight articles. One documentation skeleton. Sized for the GC's red-pen pass.",
        catalogue_match="EU AI Act Compliance Pack",
    ),
    "tool-agent-sprawl": _tool_profile(
        "tool-agent-sprawl", "audit_agents_server.html",
        "python -c 'from finance_mcp.agent_sprawl import audit_agents; audit_agents()'",
        "AGENT SPRAWL AUDITOR",
        "For the CFO at the fund staring at the Anthropic bill. The CTO at a portco whose half-shipped agent prototypes are still spending money in production.",
        "Per-agent: model, run cost, run count, last-run date, health verdict. The audit no fund runs on its own AI deployments and every fund should. The CFO finally has the line item, by tool, by model.",
        "23 agents inventoried · 16 flagged for pruning · zombies, runaway-cost, misaligned.",
        "Fifteen zombies — no successful invocation in the modeled telemetry window. One runaway-cost — over a thousand dollars per month modeled. Two misaligned — fail the eval rubric on hallucination or coverage.",
        "The inventory comes from AST-walking the server registration file — every mcp.add_tool call becomes an inventory row. Real registry, modeled telemetry.",
        "Telemetry is modeled, not measured. Per-tool token-budget estimates derived from real Anthropic published pricing — Sonnet 4.6 at three dollars per million input tokens, fifteen dollars output. The footer of the report calls this out.",
        "The pruning recommendation list is ordered by annual savings. Top entry is the CIM extractor at thirteen thousand dollars saved per year if pruned — a triple-flag agent.",
        "Total annual savings if everything flagged is pruned: nineteen thousand eight hundred eighty dollars. Modeled, not measured. Replace with real telemetry via a hook for the production version.",
        "The point isn't the exact dollar figure. The point is the discipline — every agent on a fund's roster needs cost attribution, success rate, and a renewal decision. This makes that audit one command long.",
        "Vista runs hundreds of agents. Without this, ghost agents burn budget for months unnoticed. With this, the fund's CTO has a quarterly prune-review.",
        "Pure stdlib AST walk plus pandas plus deterministic synthetic clock. No LLM at runtime. Open source · MIT.",
        "github.com/bolnet/private-equity",
        problem=(
            "The fund's CFO opens the Anthropic bill at the end of the quarter "
            "and there's no line item — just a number. Twenty-three agents "
            "running across the stack, half of them haven't shipped an artifact "
            "in thirty days, two of them are billing every day, and nobody owns "
            "the renewal decision. This tool turns that black box into a "
            "one-page audit."
        ),
        problem_hint="Per-agent: model, run cost, run count, last-run date, health verdict.",
        catalogue_match="Agent Sprawl Auditor",
    ),
    "bx-procurement": BXProfile(
        name="bx-procurement",
        report_path=REPO / "finance_output" / "benchmark_D310_FY2024.html",
        regen_hint="python -c 'from finance_mcp.procurement import benchmark_vendors; benchmark_vendors(psc_code=\"D310\", fiscal_year=2024, max_records=500)'",
        intro_kicker="PROCUREMENT × CROSS-PORTCO",
        intro_text=(
            "For the operating partner at a mid-market fund without a "
            "50-person procurement team — and the portfolio CFO whose "
            "vendor spend is bigger than their EBITDA line."
        ),
        intro_hint=(
            "Apollo's flagship value-creation lever, productized. "
            "198 real federal contracts · 28 buyers · 132 vendors · "
            "USAspending.gov public data, no auth."
        ),
        archetype_text=(
            "$936M of cross-agency savings opportunity surfaced — "
            "the same vendor priced 6× higher at one buyer than another."
        ),
        archetype_hint=(
            "Each row is a real federal agency (treated as a portco) and "
            "the dollar gap to its best-priced peer."
        ),
        rank_text=(
            "Top buyers ranked by recoverable spend if matched to their "
            "best-priced peer agency for the same vendor + service code."
        ),
        rank_hint=(
            "State Dept · GSA · Commerce — the names a partner recognises, "
            "the dollar spreads a procurement team can act on."
        ),
        top_portco_text=(
            "Department of State leads — $526.6M recoverable if it matched "
            "DHS's pricing on the same vendor."
        ),
        top_portco_hint=(
            "Two awards · $624.8M cohort spend · ~6.4× price spread vs. peer."
        ),
        peer_text=(
            "Vendor-level spread shows which suppliers price-discriminate "
            "across buyers — those are the contracts to renegotiate."
        ),
        peer_hint=(
            "Same vendor at two agencies, two prices · the data the "
            "procurement team needs is finally on one page."
        ),
        outro_text=(
            "Open source · MIT · pure stdlib + pandas. Same engine works "
            "on any portco's contract data."
        ),
        outro_hint="github.com/bolnet/private-equity",
        problem_text=(
            "Apollo's flagship value-creation lever is procurement. Fifty "
            "analysts looking at cross-portco vendor spend, finding the "
            "buyer paying six times more for the same SKU. Mid-market funds "
            "can't justify the headcount. This script is what the fifty "
            "analysts run, productized for the rest of mid-market PE."
        ),
        problem_hint="Same data Apollo uses. Same math. One-hundredth of the headcount.",
        catalogue_match="Procurement Benchmarking",
    ),
    "bx-plan-drift": BXProfile(
        name="bx-plan-drift",
        report_path=REPO / "finance_output" / "plan_drift_SoteraCo.html",
        regen_hint="python -c 'from finance_mcp.plan_drift import track_plan_drift; track_plan_drift(portco_id=\"SoteraCo\", ticker=\"SHC\")'",
        intro_kicker="100-DAY PLAN × DRIFT MONITOR",
        intro_text=(
            "For the operating partner at Day-60. The MD on the deal team "
            "who signed the 100-day plan at close. The CEO at the portco "
            "who knows three initiatives are slipping but not the dollar."
        ),
        intro_hint=(
            "Day-60 of a 100-day plan · the page the operating partner "
            "walks in with — not the tracker the consultant emailed Friday. "
            "7 frozen initiatives diffed against real Sotera Health 10-Q "
            "actuals via SEC EDGAR."
        ),
        archetype_text=(
            "Five of seven initiatives off-track. $121.6M of EBITDA at "
            "risk against the original plan."
        ),
        archetype_hint=(
            "Each initiative carries a KPI · target · due-day · plan vs. "
            "actual · dollar gap · status."
        ),
        rank_text=(
            "Initiatives ranked by recoverable dollar gap. Top miss is "
            "$85M on the dynamic-pricing pilot."
        ),
        rank_hint=(
            "The bigger the gap, the higher up the partner's intervention "
            "list it goes — automatic."
        ),
        top_portco_text=(
            "The dynamic-pricing pilot was supposed to land in 50 priority "
            "centers. The 10-Q says it didn't."
        ),
        top_portco_hint=(
            "Direction-aware gap math — positive=ahead, negative=behind, "
            "regardless of whether KPI is higher- or lower-better."
        ),
        peer_text=(
            "Operator memo at the bottom of the report — the why, the "
            "evidence, the recommended next 30 days."
        ),
        peer_hint=(
            "Same prose contract as the explainer. Defensible in a "
            "Wednesday-morning board call."
        ),
        outro_text=(
            "Reuses the SEC EDGAR fetcher. Real public actuals. "
            "Reproducible from a clean clone."
        ),
        outro_hint="github.com/bolnet/private-equity",
        problem_text=(
            "Day-Sixty of a hundred-day plan. The board is in seven weeks. "
            "The operating partner knows three initiatives are slipping but "
            "doesn't know the dollar. Plan drift gets surfaced today by the "
            "consultant on Monday morning of the QBR — by then it's been "
            "drifting for six weeks. This tool catches it at Day-Sixty."
        ),
        problem_hint="Real public 10-Q actuals. Direction-aware gap math. The page the consultant didn't ship.",
        catalogue_match="100-Day Plan Drift Monitor",
    ),
    "bx-hmda-states": BXProfile(
        name="bx-hmda-states",
        report_path=REPO / "finance_output" / "bx_report_hmda_states.html",
        regen_hint="python -m scripts.build_bx_hmda_states",
        intro_kicker="BX · CROSS-PORTCO BENCHMARK",
        intro_text=(
            "For the managing partner writing the LP letter. The fund "
            "operating partner asked: is this pattern fund-wide or "
            "one-portco? The IR seat drafting the operational-alpha exhibit."
        ),
        intro_hint=(
            "5 regional mortgage origination portcos · DC · DE · MA · AZ · "
            "GA · all real CFPB HMDA 2023 · $184M fund-wide identifiable."
        ),
        archetype_text=(
            "Fund-wide archetype distribution — pricing × selection × "
            "allocation, P10 / median / P90 across all 5 portcos."
        ),
        archetype_hint=(
            "Same lending_b2c template applied to every portco · "
            "apples-to-apples comparison."
        ),
        rank_text=(
            "Fund-level rank table. Each portco scored against fund "
            "mean, median, P10, P90."
        ),
        rank_hint=(
            "Five mortgage markets ranked by total identifiable annual $ "
            "impact · LP-grade exhibit."
        ),
        top_portco_text=(
            "HMDA_GA leads the fund — $106M of identifiable impact "
            "across pricing × selection × allocation."
        ),
        top_portco_hint=(
            "153k mortgage applications · 17% denial rate · "
            "concentration in B-grade 360-month."
        ),
        peer_text=(
            "Peer groups by cosine similarity on archetype-profile shape."
        ),
        peer_hint=(
            "Top-3 most similar portcos surface fund-wide themes — "
            "patterns that repeat across markets, not one-off."
        ),
        outro_text=(
            "Open source · MIT · pandas-deterministic. Same engine that "
            "ran each portco's diagnostic."
        ),
        outro_hint="github.com/bolnet/private-equity",
        problem_text=(
            "The managing partner is writing the LP letter. Page two needs "
            "an *operational alpha* exhibit. The fund operating partner is "
            "asked: is this pattern fund-wide or one-portco? Today they "
            "answer in Excel, by hand, across PDFs the portcos sent in "
            "different formats. This tool reads the diagnostic JSON each "
            "portco already produced and rolls them up into one comparable "
            "view."
        ),
        problem_hint="The exhibit no scorecard vendor ships, because no scorecard vendor sees the row data.",
        catalogue_match="Cross-Portco Benchmarking",
    ),
    "bx-mixed-fund": BXProfile(
        name="bx-mixed-fund",
        report_path=REPO / "finance_output" / "bx_report_mixed_fund.html",
        regen_hint="python -m scripts.build_bx_mixed_fund",
        intro_kicker="BX · MIXED-VERTICAL FUND",
        intro_text=(
            "For the managing partner — the cross-vertical case. 7 portcos "
            "across consumer + mortgage lending. The exhibit no scorecard "
            "vendor ships, because no scorecard vendor sees the row data."
        ),
        intro_hint=(
            "5 Lending Club regional · 1 Yasserh mortgage · 1 HMDA DC · "
            "all real public data · $1.14B fund-wide identifiable."
        ),
        archetype_text=(
            "Fund-wide archetype distribution shows pricing × selection × "
            "allocation across both verticals."
        ),
        archetype_hint=(
            "BX surfaces patterns that repeat across verticals — "
            "not just within one."
        ),
        rank_text=(
            "Fund-level rank table — mortgage portcos dominate consumer "
            "by 100x because mortgage loan amounts are 100x bigger."
        ),
        rank_hint=(
            "Honest cross-vertical observation — BX shows scale "
            "differences a partner would miss in a manual review."
        ),
        top_portco_text=(
            "MortgageCo leads at $1.1B · the cross-vertical lesson is "
            "that mortgage scale dominates everything else."
        ),
        top_portco_hint=(
            "30k real US mortgages · 24% default · loss concentration in "
            "specific loan-type × region cells."
        ),
        peer_text=(
            "Peer groups cluster portcos with similar archetype profiles."
        ),
        peer_hint=(
            "Mortgage portcos cluster together · consumer-lending "
            "regionals cluster together · same vertical patterns."
        ),
        outro_text=(
            "Two engines · one toolchain · five real public datasets. "
            "Same DX runs everywhere."
        ),
        outro_hint="github.com/bolnet/private-equity",
        problem_text=(
            "What happens when you mix verticals? Five consumer-lending "
            "portcos, two mortgage portcos, loan sizes that differ by two "
            "orders of magnitude. The naive scorecard ranks the mortgage "
            "portcos at the top and calls it a day. This tool surfaces "
            "the *archetype profile* — the shape of decision quality — "
            "and clusters portcos by shape, not by absolute scale."
        ),
        problem_hint="Mortgage scale dominates. The right metric is impact-as-percent-of-EBITDA, not absolute dollars.",
        catalogue_match="Cross-Portco Benchmarking",
    ),
}


PROFILES: dict[str, Profile] = {
    "lending": Profile(
        name="lending",
        portco_id="LendingCo",
        loans=REPO / "demo" / "lending_club" / "loans.csv",
        perf=REPO / "demo" / "lending_club" / "performance.csv",
        regen_hint="python -m demo.lending_club.slice",
        intro_kicker="DECISION DIAGNOSTIC × DX",
        intro_text=(
            "For the operating partner in the first 30 days after close — "
            "and the CFO at the portco who needs an auditable answer to "
            "'where are we leaving money on the table?'"
        ),
        intro_hint=(
            "One tool · one upload · one auditable answer. Pandas math. "
            "Row-level evidence on every dollar."
        ),
        entities_text=(
            "loans.csv (underwriting) + performance.csv (servicing) "
            "— joined on loan_id."
        ),
        entities_hint="Auto-matched to the lending_b2c vertical template.",
        finding_text=(
            "Annualized $ impact identified · ranked opportunities · "
            "row-level evidence."
        ),
        finding_hint=(
            "All on real Lending Club originations — public data, not "
            "synthetic."
        ),
        opp_text=(
            "Segment definition · annualized $ impact · persistence "
            "quarters · counterfactual."
        ),
        opp_hint="Sub-prime grades × refi loans persistently lose money — quantified.",
    ),
    "hmda": Profile(
        name="hmda",
        portco_id="DCMortgage",
        loans=REPO / "demo" / "hmda_dc" / "loans.csv",
        perf=REPO / "demo" / "hmda_dc" / "performance.csv",
        regen_hint=(
            "curl -sSL -o /tmp/hmda_dc_2023.csv "
            "'https://ffiec.cfpb.gov/v2/data-browser-api/view/csv"
            "?years=2023&states=DC&actions_taken=1,3' && "
            "python -m demo.hmda_dc.slice"
        ),
        intro_kicker="DECISION DIAGNOSTIC × DX",
        intro_text=(
            "For the operating partner staring at a portco with adverse-"
            "selection no one's quantified — and the CRO who knows the "
            "denial book is leaking money but not which cohort."
        ),
        intro_hint=(
            "Real CFPB HMDA · Washington DC · 2023 · 11.6k mortgage "
            "applications · public regulatory disclosure."
        ),
        entities_text=(
            "loans.csv (HMDA application data) + performance.csv "
            "(modeled cashflows) — joined on loan_id."
        ),
        entities_hint=(
            "Decision columns: loan_type · loan_purpose · MSA · "
            "loan_product_type · lien_status."
        ),
        finding_text=(
            "Adverse-selection clusters where denial cost + thin pricing "
            "outweigh the booked spread."
        ),
        finding_hint=(
            "Real DC mortgage applications · 2,994 denials · 8,616 "
            "originations · pure CFPB data."
        ),
        opp_text=(
            "Each opp surfaces a (purpose × MSA × product) cohort where "
            "underwriting decisions are systematically poor."
        ),
        opp_hint=(
            "Application-time decisions, dollar-quantified, "
            "with row-level evidence."
        ),
    ),
    "yasserh": Profile(
        name="yasserh",
        portco_id="MortgageCo",
        loans=REPO / "demo" / "yasserh_mortgages" / "loans.csv",
        perf=REPO / "demo" / "yasserh_mortgages" / "performance.csv",
        regen_hint="python -m demo.yasserh_mortgages.slice",
        intro_kicker="DECISION DIAGNOSTIC × DX",
        intro_text=(
            "For the deal partner who just signed a specialty-mortgage "
            "platform and the operating partner walking in with a 100-day "
            "plan that needs a number on page two."
        ),
        intro_hint=(
            "Real US specialty-mortgage book · 148,670 originations · "
            "CC0 public data · same engine, different vertical."
        ),
        entities_text=(
            "loans.csv (underwriting) + performance.csv (servicing) "
            "— joined on loan_id."
        ),
        entities_hint=(
            "Mapped onto lending_b2c — DX doesn't care about the source "
            "format."
        ),
        finding_text=(
            "Annualized $ impact identified · ranked opportunities · "
            "row-level evidence."
        ),
        finding_hint=(
            "On 30k real US mortgage originations · 24% default rate "
            "· $1.1B at risk."
        ),
        opp_text=(
            "Segment definition · annualized $ impact · persistence "
            "quarters · counterfactual."
        ),
        opp_hint=(
            "Adverse-selection clusters in loan_type × Region × "
            "submission_channel — quantified."
        ),
    ),
}


def _run_recording(profile_name: str, scenes_fn) -> None:
    """Wrap a scene callable in the headed-Chromium video-recording context.
    `scenes_fn` takes a Page and runs the scripted scenes."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=150)
        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            record_video_dir=str(OUT),
            record_video_size={"width": 1400, "height": 900},
        )
        page = context.new_page()
        scenes_fn(page)
        context.close()
        browser.close()
    webms = sorted(OUT.glob("*.webm"), key=lambda p: p.stat().st_mtime)
    if not webms:
        sys.exit("Recording failed — no .webm found.")
    latest = webms[-1]
    print(f"[record · {profile_name}] Saved: {latest} ({latest.stat().st_size / 1e6:.1f} MB)")
    print(
        f"[record · {profile_name}] Convert to MP4 (optional): "
        f"ffmpeg -y -i {latest} -c:v libx264 -crf 20 -preset slow "
        f"{latest.with_suffix('.mp4')}"
    )


def _record_generic_report_walkthrough(profile: BXProfile, page: Page) -> None:
    """Podcast-style ~5:00 walkthrough: problem framing → UI catalogue →
    per-tool deep dive over the rendered report.

    Beats 0a-0c (~45s) are the pre-tour: the host frames the PE pain,
    shows the catalogue UI, then highlights this tool's tile. Beats 1-14
    (~3:30) are the existing per-tool deep dive over the rendered HTML.

    Designed as a partner-grade explainer, not a feature demo. The voice
    talent delivers the matching beat from .local/demo/voiceover-scripts.md
    over each scene's caption.

    Uses permissive selectors with fallbacks: h1 (always present),
    .stats-strip (most reports), h2:nth-of-type(N), first table, last h2.
    Works for the explainer / CIM / DDQ / normalize / eval / EU AI Act /
    agent-sprawl / seller-pack reports without per-tool selector knowledge.
    """
    if not profile.report_path.exists():
        raise SystemExit(
            f"Report missing at {profile.report_path}. Build it with:\n  {profile.regen_hint}"
        )
    url = f"file://{profile.report_path.resolve()}"

    # ============ PRE-TOUR (3 beats, ~45s) ============
    # Beat 0a: Topic intro — frame the partner pain (~16s)
    catalogue_url = "http://localhost:8765/app/tools.html"
    try:
        page.goto(catalogue_url, wait_until="networkidle", timeout=8000)
        catalogue_loaded = True
    except Exception:
        catalogue_loaded = False
    if catalogue_loaded and profile.problem_text:
        set_caption(
            page,
            "THE PROBLEM",
            profile.problem_text,
            profile.problem_hint,
        )
        time.sleep(16.0)

        # Beat 0b: Show the catalogue UI overview (~15s)
        page.evaluate(
            "(() => { const t = document.querySelector('.ticker, .masthead-meta');"
            "  if (t) t.scrollIntoView({behavior:'smooth', block:'center'}); })()"
        )
        set_caption(
            page,
            "THE STACK",
            "Twelve tools across the deal lifecycle — wedge, moat, diligence, operate. One stack, one design language, one auditable contract.",
            "Each tile maps to a tool that ships an artifact a partner can defend in a Wednesday-morning meeting.",
        )
        time.sleep(15.0)

        # Beat 0c: highlight this tool's tile in the catalogue (~14s)
        if profile.catalogue_match:
            match_js = profile.catalogue_match.replace("\\", "\\\\").replace("'", "\\'")
            page.evaluate(
                "(() => {"
                f"  const target = '{match_js}';"
                "  const all = [...document.querySelectorAll('.entry-name, h2')];"
                "  const t = all.find(e => e.textContent.includes(target));"
                "  if (!t) return;"
                "  const article = t.closest('article') || t.parentElement;"
                "  if (article) {"
                "    article.style.transition = 'outline 0.5s ease, box-shadow 0.5s ease';"
                "    article.style.outline = '2px solid rgba(107,20,20,0.40)';"
                "    article.style.boxShadow = '0 0 0 8px rgba(107,20,20,0.06)';"
                "    article.scrollIntoView({behavior:'smooth', block:'center'});"
                "    setTimeout(() => { article.style.outline = ''; article.style.boxShadow = ''; }, 22000);"
                "  } else { t.scrollIntoView({behavior:'smooth', block:'center'}); }"
                "})()"
            )
            set_caption(
                page,
                "THIS ONE",
                f"This is the tool we're walking into now. {profile.intro_text}",
                "Look at the tile — that's the entry point. Below the title is the form, and below the form is the button that generates the document.",
            )
            time.sleep(14.0)

            # Beat 0d: FILL the form — text fields, file drops, selects.
            # The form's submit handler in tools.html POSTs to /api/<tool>;
            # on success it auto-navigates to the freshly-rendered report.
            set_caption(
                page,
                "THE INPUTS",
                "Drop the portco's CSVs · type the parameters · pick the audience. The engine does the rest.",
                "No data leaves the operator's laptop. Pandas math underneath; the language model only narrates what the math already proved.",
            )

            # File inputs (drop CSVs) — populate via Playwright's set_input_files
            tool_demo_files = {
                "tool-explainer":   [REPO / "demo" / "yasserh_mortgages" / "loans.csv",
                                     REPO / "demo" / "yasserh_mortgages" / "performance.csv"],
                "tool-seller-pack": [REPO / "demo" / "yasserh_mortgages" / "loans.csv",
                                     REPO / "demo" / "yasserh_mortgages" / "performance.csv"],
            }
            files_for_profile = tool_demo_files.get(profile.name, [])
            files_for_profile = [p for p in files_for_profile if p.exists()]
            if files_for_profile:
                try:
                    file_input = page.locator(
                        f'article:has(h2:has-text("{profile.catalogue_match}")) input[type="file"]'
                    ).first
                    file_input.scroll_into_view_if_needed(timeout=2000)
                    file_input.set_input_files([str(p) for p in files_for_profile])
                except Exception:
                    pass
            # Type into every visible text input within this article
            text_inputs = page.locator(
                f'article:has(h2:has-text("{profile.catalogue_match}")) input[type="text"]'
            )
            try:
                count = text_inputs.count()
            except Exception:
                count = 0
            for i in range(count):
                el = text_inputs.nth(i)
                try:
                    placeholder = el.get_attribute("placeholder") or ""
                except Exception:
                    placeholder = ""
                # Use the placeholder as the typed value when available — it
                # is already a real example path on each form.
                value = placeholder or "MortgageCo"
                try:
                    el.scroll_into_view_if_needed(timeout=2000)
                    el.click()
                    el.fill("")
                    el.type(value, delay=70)
                except Exception:
                    pass
            # Toggle any select to show interactivity (audience switch on the
            # explainer; otherwise harmless on tools without a select).
            try:
                sel = page.locator(
                    f'article:has(h2:has-text("{profile.catalogue_match}")) select'
                ).first
                if sel.count() > 0:
                    sel.scroll_into_view_if_needed(timeout=1500)
                    options = sel.locator("option").all_inner_texts()
                    if len(options) > 1:
                        sel.select_option(index=1)
                        time.sleep(1.2)
                        sel.select_option(index=0)
            except Exception:
                pass
            time.sleep(7.0)

            # Beat 0e: CLICK the Render button — the real /api/<tool>
            # endpoint runs, the JS in tools.html shows a "Generating…"
            # status with elapsed timer, then navigates to the freshly-
            # rendered report (~25s — covers tool execution + transition).
            set_caption(
                page,
                "THE GENERATION",
                "One click. The engine runs, the document renders, the artifact lands. What you see next is what gets shipped — letterpress design, every figure citable.",
                "Same input, same output — every time. Run it twice; the renderer raises if anything drifts.",
            )
            # Optional: delete the cached output to *prove* the recorder
            # captures a fresh generation.
            try:
                if profile.report_path.exists():
                    profile.report_path.unlink()
            except Exception:
                pass
            try:
                btn = page.locator(
                    f'article:has(h2:has-text("{profile.catalogue_match}")) button.run-btn'
                ).first
                btn.scroll_into_view_if_needed(timeout=2000)
                # Pulse the button briefly before the click for visual emphasis
                page.evaluate(
                    "(() => {"
                    f"  const target = '{match_js}';"
                    "  const all = [...document.querySelectorAll('.entry-name, h2')];"
                    "  const t = all.find(e => e.textContent.includes(target));"
                    "  if (!t) return;"
                    "  const article = t.closest('article');"
                    "  if (!article) return;"
                    "  const b = article.querySelector('.run-btn');"
                    "  if (b) {"
                    "    b.style.transition = 'transform 0.3s ease, box-shadow 0.3s ease';"
                    "    b.style.transform = 'scale(1.06)';"
                    "    b.style.boxShadow = '0 0 0 4px rgba(107,20,20,0.20), 0 8px 24px -4px rgba(107,20,20,0.40)';"
                    "  }"
                    "})()"
                )
                time.sleep(2.0)
                btn.click(timeout=4000)
            except Exception:
                pass
            # Wait for the live tool to complete + the JS to navigate.
            # Most tools finish in 2-8s; eval and audit-agents can take
            # up to 30s. Wait for the URL to change off /app/tools.html
            # then for the report to be loaded.
            try:
                page.wait_for_url(
                    lambda u: "/app/tools.html" not in u,
                    timeout=45000,
                )
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                # If the click never navigated, fall back to the cached
                # report so the deep-dive still records something useful.
                page.goto(url, wait_until="networkidle")
            time.sleep(7.0)

    # ============ DEEP DIVE (14 beats, ~3:30) ============
    # Scene 1: cold open — who this is for (audience callout) (~14s)
    # If the catalogue submit-override navigated us to the example URL we may
    # already be on the report page; goto is idempotent and robust either way.
    page.goto(url, wait_until="networkidle")
    set_caption(page, profile.intro_kicker, profile.intro_text, profile.intro_hint)
    time.sleep(14.0)

    # Scene 2: scroll to H1 (~15s) — the headline figure, "underline this"
    page.evaluate(
        "(() => { const h = document.querySelector('h1');"
        "  if (!h) return;"
        "  h.scrollIntoView({behavior:'smooth', block:'start'});"
        "  h.style.transition = 'background 0.6s ease';"
        "  setTimeout(() => { h.style.background = 'rgba(107,20,20,0.06)'; }, 800);"
        "  setTimeout(() => { h.style.background = ''; }, 3500);"
        "})()"
    )
    set_caption(page, "I. THE HEADLINE", profile.archetype_text, profile.archetype_hint)
    time.sleep(15.0)

    # Scene 3: universal "why this exists" beat (~14s)
    set_caption(
        page,
        "II. WHY THIS EXISTS",
        "The work this tool replaces is partner-time and associate-time — the late-night email, the consultant deck, the vendor demo that never quite fit.",
        "The tool ships the artifact a partner would otherwise pay six figures to commission.",
    )
    time.sleep(14.0)

    # Scene 4: stats-strip (~15s) — the numerical block under the headline
    page.evaluate(
        "(() => { const s = document.querySelector('.stats-strip, .stat-row, .meta');"
        "  if (s) { s.scrollIntoView({behavior:'smooth', block:'center'});"
        "    s.style.transition = 'box-shadow 0.5s ease';"
        "    setTimeout(() => { s.style.boxShadow = '0 0 0 2px rgba(107,20,20,0.18)'; }, 700);"
        "    setTimeout(() => { s.style.boxShadow = ''; }, 3500); }"
        "  else document.querySelectorAll('h2')[0]?.scrollIntoView({behavior:'smooth', block:'start'});"
        "})()"
    )
    set_caption(page, "III. THE NUMBERS UNDER THE HEADLINE", profile.rank_text, profile.rank_hint)
    time.sleep(15.0)

    # Scene 5: methodology / "every figure traces" universal beat (~14s)
    set_caption(
        page,
        "IV. THE CONTRACT",
        "Every figure on the page traces to a source row. No prose invented. No model arithmetic. The renderer raises before it ships a number it can't ground.",
        "Pandas math underneath. The language model only narrates what the math already proved.",
    )
    time.sleep(14.0)

    # Scene 6: first H2 section (~16s)
    page.evaluate(
        "document.querySelectorAll('h2')[0]?.scrollIntoView({behavior:'smooth', block:'start'});"
    )
    set_caption(page, "V. THE FIRST SECTION", profile.top_portco_text, profile.top_portco_hint)
    time.sleep(16.0)

    # Scene 7: highlight a key row / flag / opportunity card (~16s)
    page.evaluate(
        "(() => { const tr = document.querySelector('table tbody tr:nth-child(2), .flag, .opp-section, .opp, .summary, .arche-row');"
        "  if (tr) { tr.style.transition = 'background 0.6s ease, outline 0.4s ease';"
        "    tr.style.background = 'rgba(138,111,26,0.10)';"
        "    tr.style.outline = '1px solid rgba(107,20,20,0.30)';"
        "    tr.scrollIntoView({behavior:'smooth', block:'center'});"
        "    setTimeout(() => { tr.style.background = ''; tr.style.outline = ''; }, 9000); }"
        "})()"
    )
    set_caption(page, "VI. ONE ROW DRILLED", profile.peer_text, profile.peer_hint)
    time.sleep(16.0)

    # Scene 8: universal "what a partner does with this" beat (~14s)
    set_caption(
        page,
        "VII. WHAT A PARTNER DOES WITH IT",
        "The page is sized for one read — fifteen seconds for the headline, two minutes for the per-row evidence, one decision walked into the next meeting.",
        "Not a dashboard. An artifact. The output of one tool, scoped for one decision.",
    )
    time.sleep(14.0)

    # Scene 9: second H2 section (~16s)
    page.evaluate(
        "(() => { const all = document.querySelectorAll('h2');"
        "  if (all.length > 1) all[1].scrollIntoView({behavior:'smooth', block:'start'});"
        "  else if (all.length > 0) all[0].scrollIntoView({behavior:'smooth', block:'end'});"
        "})()"
    )
    set_caption(
        page,
        "VIII. THE NEXT SECTION",
        "The second view widens the lens — from one row to the pattern, from the pattern to the methodology behind it.",
        "Each section is independently citable. A diligence VP can lift any one of them into a memo.",
    )
    time.sleep(16.0)

    # Scene 10: highlight a second region — a card / paragraph / table (~14s)
    page.evaluate(
        "(() => { const cards = document.querySelectorAll('.peer-card, .opp, .article-card, .flag, .summary, .opp-narr');"
        "  if (cards.length > 1) { const c = cards[1];"
        "    c.style.transition = 'background 0.6s ease';"
        "    c.style.background = 'rgba(107,20,20,0.05)';"
        "    c.scrollIntoView({behavior:'smooth', block:'center'});"
        "    setTimeout(() => { c.style.background = ''; }, 9000); }"
        "})()"
    )
    set_caption(
        page,
        "IX. A SECOND VIEW",
        "Same evidence, different cut. The viewer can pick the cut that matches the question they're holding — board memo or operator memo, exit pack or DDQ, fund-wide or per-portco.",
        "The numbers are identical across cuts. The renderer enforces it.",
    )
    time.sleep(14.0)

    # Scene 11: universal "reproducibility / methodology" beat (~14s)
    set_caption(
        page,
        "X. REPRODUCIBILITY",
        "Run the same input twice — identical output. Same archive, same memo. The science of it isn't optional; it's how the artifact survives a Tuesday-morning IC challenge.",
        "Audit-grade by construction. Open the source file, the math is in plain Python.",
    )
    time.sleep(14.0)

    # Scene 12: footer / colophon scroll (~13s)
    page.evaluate("window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})")
    set_caption(
        page,
        "XI. THE COLOPHON",
        "Every report names its source artifact at the foot of the page — file path, build command, methodology link. Hand the report to anyone; they can rebuild it from a clean clone.",
        "The footer is the audit trail. It travels with the file.",
    )
    time.sleep(13.0)

    # Scene 13: scroll back to the top — final headline read (~12s)
    page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
    set_caption(
        page,
        "XII. WHO PICKS IT UP NEXT",
        "The artifact lands on the seat that needed it — the deal partner, the operating partner, the GC, the IR seat, the portco CFO. One tool, one owner, one decision.",
        "Sized for the org chart, not for the dashboard.",
    )
    time.sleep(12.0)

    # Scene 14: outro — open source / CTA (~13s)
    set_caption(page, "OPEN SOURCE · MIT", profile.outro_text, profile.outro_hint)
    time.sleep(13.0)


def _record_bx_walkthrough(profile: BXProfile, page: Page) -> None:
    """8-scene scroll-through of a rendered BX corpus HTML report."""
    if not profile.report_path.exists():
        raise SystemExit(
            f"BX report missing at {profile.report_path}. Build it with:\n"
            f"  {profile.regen_hint}"
        )
    url = f"file://{profile.report_path.resolve()}"

    # ---- Scene 1: open report — intro (~6s) ----
    page.goto(url, wait_until="networkidle")
    set_caption(page, profile.intro_kicker, profile.intro_text, profile.intro_hint)
    time.sleep(6.0)

    # ---- Scene 2: scroll to title H1 (~5s) ----
    page.evaluate("document.querySelector('h1').scrollIntoView({behavior:'smooth', block:'start'})")
    set_caption(page, "STEP 1 · FUND HEADLINE",
                "Total portcos · total identifiable $ — the LP-letter top line.", "")
    time.sleep(5.0)

    # ---- Scene 3: archetype distribution (~7s) ----
    page.evaluate(
        "const els = document.querySelectorAll('h2');"
        "for (const e of els) { if (e.textContent.includes('archetype')) "
        "  { e.scrollIntoView({behavior:'smooth', block:'start'}); break; } }"
    )
    set_caption(page, "STEP 2 · ARCHETYPE DISTRIBUTION",
                profile.archetype_text, profile.archetype_hint)
    time.sleep(7.0)

    # ---- Scene 4: rank table (~7s) ----
    page.evaluate(
        "const t = document.querySelector('.rank-tbl');"
        "if (t) t.scrollIntoView({behavior:'smooth', block:'start'});"
    )
    set_caption(page, "STEP 3 · FUND-LEVEL RANK TABLE",
                profile.rank_text, profile.rank_hint)
    time.sleep(7.0)

    # ---- Scene 5: highlight top portco row (~5s) ----
    page.evaluate(
        "const r = document.querySelectorAll('.rank-cell')[0];"
        "if (r) { const tr = r.closest('tr'); if (tr) "
        "  { tr.style.background = 'rgba(245,215,110,0.12)'; "
        "    tr.scrollIntoView({behavior:'smooth', block:'center'}); } }"
    )
    set_caption(page, "STEP 4 · TOP PORTCO",
                profile.top_portco_text, profile.top_portco_hint)
    time.sleep(5.0)

    # ---- Scene 6: peer groups (~6s) ----
    page.evaluate(
        "const els = document.querySelectorAll('h2');"
        "for (const e of els) { if (e.textContent.toLowerCase().includes('peer')) "
        "  { e.scrollIntoView({behavior:'smooth', block:'start'}); break; } }"
    )
    set_caption(page, "STEP 5 · PEER GROUPS",
                profile.peer_text, profile.peer_hint)
    time.sleep(6.0)

    # ---- Scene 7: highlight a peer card (~5s) ----
    page.evaluate(
        "const c = document.querySelector('.peer-card');"
        "if (c) { c.style.outline = '2px solid rgba(245,215,110,0.5)'; "
        "  c.scrollIntoView({behavior:'smooth', block:'center'}); }"
    )
    set_caption(page, "STEP 6 · ONE PEER GROUP",
                "Each card lists the 3 most-similar portcos by archetype-profile cosine.",
                "Patterns that repeat across portcos are fund-wide themes.")
    time.sleep(5.0)

    # ---- Scene 8: scroll back to top — outro (~5s) ----
    page.evaluate("window.scrollTo({top:0, behavior:'smooth'})")
    set_caption(page, "OPEN SOURCE · MIT", profile.outro_text, profile.outro_hint)
    time.sleep(5.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    all_profiles = sorted(list(PROFILES) + list(BX_PROFILES))
    parser.add_argument(
        "--profile",
        choices=all_profiles,
        default="lending",
        help="Which demo profile to record (default: lending)",
    )
    args = parser.parse_args()

    if args.profile in BX_PROFILES:
        bx_profile = BX_PROFILES[args.profile]
        if bx_profile.mode == "report":
            _run_recording(args.profile, lambda page: _record_generic_report_walkthrough(bx_profile, page))
        else:
            _run_recording(args.profile, lambda page: _record_bx_walkthrough(bx_profile, page))
        return

    profile = PROFILES[args.profile]
    if not profile.loans.exists() or not profile.perf.exists():
        sys.exit(
            f"Demo CSVs missing for profile '{profile.name}'. Regenerate with:\n"
            f"  {profile.regen_hint}\n"
            f"Looking for: {profile.loans} + {profile.perf}"
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=150)
        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            record_video_dir=str(OUT),
            record_video_size={"width": 1400, "height": 900},
        )
        page = context.new_page()

        # ============ PRE-TOUR (3 beats over the catalogue, ~46s) ============
        catalogue_url = "http://localhost:8765/app/tools.html"
        try:
            page.goto(catalogue_url, wait_until="networkidle", timeout=8000)
            catalogue_ok = True
        except Exception:
            catalogue_ok = False

        if catalogue_ok:
            # Beat 0a: the partner pain (~16s)
            set_caption(
                page,
                "THE PROBLEM",
                "Today I want to talk about the moment an operating partner walks into a portco — first thirty days after close, board meeting in nine weeks, no defensible answer to the simplest question: where is this business leaking money?",
                "The work this video shows is the answer that lands on the partner's desk before the consultant arrives.",
            )
            time.sleep(16.0)

            # Beat 0b: stack overview (~15s)
            page.evaluate(
                "(() => { const t = document.querySelector('.ticker, .masthead-meta');"
                "  if (t) t.scrollIntoView({behavior:'smooth', block:'center'}); })()"
            )
            set_caption(
                page,
                "THE STACK",
                "Twelve tools across the deal lifecycle — wedge, moat, diligence, operate. One stack, one design language, one auditable contract.",
                "Each tile maps to a tool that ships an artifact a partner can defend in a Wednesday-morning meeting.",
            )
            time.sleep(15.0)

            # Beat 0c: highlight the DX tile (~15s)
            page.evaluate(
                "(() => {"
                "  const target = 'Decision Diagnostic';"
                "  const all = [...document.querySelectorAll('.entry-name, h2')];"
                "  const t = all.find(e => e.textContent.includes(target));"
                "  if (!t) return;"
                "  const article = t.closest('article') || t.parentElement;"
                "  if (article) {"
                "    article.style.transition = 'outline 0.5s ease, box-shadow 0.5s ease';"
                "    article.style.outline = '2px solid rgba(107,20,20,0.40)';"
                "    article.style.boxShadow = '0 0 0 8px rgba(107,20,20,0.06)';"
                "    article.scrollIntoView({behavior:'smooth', block:'center'});"
                "    setTimeout(() => { article.style.outline = ''; article.style.boxShadow = ''; }, 14000);"
                "  } else { t.scrollIntoView({behavior:'smooth', block:'center'}); }"
                "})()"
            )
            set_caption(
                page,
                "TOOL I · DECISION DIAGNOSTIC",
                "First tool in the stack. The wedge. Drop the portco's CSVs, the engine surfaces the dollar story — auditable, row-level, defensible. Let's run it on real Lending Club data.",
                "Engine: pandas, deterministic. The language model only narrates what the math already proved.",
            )
            time.sleep(15.0)

        # ============ DX UPLOAD FLOW (extended dwells) ============
        # ---- Scene 1: open /app/ — intro caption (~12s) ----
        page.goto(URL_APP, wait_until="networkidle")
        set_caption(
            page,
            profile.intro_kicker,
            profile.intro_text,
            profile.intro_hint,
        )
        time.sleep(12.0)

        # ---- Scene 2: type portco id (~4s) ----
        page.locator("#portcoId").click()
        page.locator("#portcoId").type(profile.portco_id, delay=130)
        set_caption(
            page,
            "STEP 1 · IDENTIFY THE PORTCO",
            "A label for this engagement — travels with every artifact downstream. Memo, JSON sidecar, BX rollup. Every dollar attribution traces back to this name.",
            "",
        )
        time.sleep(10.0)

        # ---- Scene 3: drag-drop the CSVs (~16s) ----
        set_caption(
            page,
            "STEP 2 · DROP THE CSVs",
            profile.entities_text,
            profile.entities_hint,
        )
        time.sleep(2.0)
        drop_files_visibly(page, "#dropZone", [profile.loans, profile.perf])
        time.sleep(13.0)

        # ---- Scene 4: click Run, show pipeline (~14s + wait for completion) ----
        page.locator("#runBtn").click()
        set_caption(
            page,
            "STEP 3 · THE SEVEN STAGES",
            "ingest → segment stats → time stability → counterfactual → evidence → memo → report.",
            "Pure pandas. The language model never touches arithmetic. Every stage is a deterministic MCP tool.",
        )
        page.wait_for_selector(
            "#resultSummary:has-text('projected impact')", timeout=120_000
        )
        time.sleep(4.0)

        # ---- Scene 5: result summary (~13s) ----
        page.evaluate(
            "document.querySelector('#result').scrollIntoView({behavior:'smooth', block:'start'})"
        )
        set_caption(
            page,
            "STEP 4 · THE FINDING",
            profile.finding_text,
            profile.finding_hint,
        )
        time.sleep(13.0)

        # ---- Scene 6: drill into the top opportunity card (~14s) ----
        page.evaluate(
            "(() => {"
            "  const f=document.querySelector('#reportFrame');"
            "  if (!f) return;"
            "  const doc=f.contentDocument;"
            "  const opp=doc.querySelector('.opp');"
            "  if (opp) {"
            "    opp.scrollIntoView({behavior:'smooth', block:'start'});"
            "    opp.style.transition = 'background 0.5s ease';"
            "    setTimeout(() => { opp.style.background = 'rgba(107,20,20,0.05)'; }, 600);"
            "    setTimeout(() => { opp.style.background = ''; }, 9000);"
            "  }"
            "})()"
        )
        set_caption(
            page,
            "STEP 5 · INSIDE THE OPPORTUNITY",
            profile.opp_text,
            profile.opp_hint,
        )
        time.sleep(14.0)

        # ---- Scene 7: zoom into the memo prose (~14s) ----
        page.evaluate(
            "(() => {"
            "  const f=document.querySelector('#reportFrame');"
            "  if (!f) return;"
            "  const doc=f.contentDocument;"
            "  const narr=doc.querySelector('.opp-narr');"
            "  if (narr) narr.scrollIntoView({behavior:'smooth', block:'center'});"
            "})()"
        )
        set_caption(
            page,
            "STEP 6 · BOARD + OPERATOR MEMOS",
            "Each opportunity ships with two narrative views — the why, the counterfactual, the risk of inaction, the rollout.",
            "Defensible language a managing director can read into the board meeting on Wednesday — and the operator can act on Thursday morning.",
        )
        time.sleep(14.0)

        # ---- Scene 8: the contract / reproducibility (~13s) ----
        set_caption(
            page,
            "STEP 7 · THE CONTRACT",
            "Run the same upload twice — identical output. Same JSON sidecar, same memo, same row IDs. The tool is deterministic by construction.",
            "An LP audit picks up the JSON, walks the row IDs, and rebuilds the entire dollar story from the underlying CSV. This is what defensible looks like.",
        )
        time.sleep(13.0)

        # ---- Scene 9: closing (~12s) ----
        page.evaluate(
            "document.querySelector('.head').scrollIntoView({behavior:'smooth', block:'start'})"
        )
        set_caption(
            page,
            "OPEN SOURCE · MIT · PANDAS-DETERMINISTIC",
            "Tool I of twelve. The wedge into a portco's data — then move to the moat. Cross-portco benchmark, exit proof pack, eleven more tools on the same engine.",
            "github.com/bolnet/private-equity",
        )
        time.sleep(12.0)

        context.close()
        browser.close()

    webms = sorted(OUT.glob("*.webm"), key=lambda p: p.stat().st_mtime)
    if not webms:
        sys.exit("Recording failed — no .webm found.")
    latest = webms[-1]
    print(f"[record] Saved: {latest} ({latest.stat().st_size / 1e6:.1f} MB)")
    print(
        f"[record] Convert to MP4 (optional): "
        f"ffmpeg -y -i {latest} -c:v libx264 -crf 20 -preset slow "
        f"{latest.with_suffix('.mp4')}"
    )


if __name__ == "__main__":
    main()
