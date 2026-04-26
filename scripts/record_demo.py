"""
Record a scripted walkthrough of the DX upload UI or the BX cross-portco
benchmark report, on real public data. Produces a .webm file that can be
converted to MP4.

Profiles ship in two flavours:

    DX (upload-driven, requires the local web server):
        python scripts/record_demo.py --profile lending   # default
        python scripts/record_demo.py --profile yasserh
        python scripts/record_demo.py --profile hmda

    BX (navigation-only over a rendered fund-level report, no server):
        python scripts/record_demo.py --profile bx-hmda-states
        python scripts/record_demo.py --profile bx-mixed-fund

DX needs the server running first:
    python -m finance_mcp.web 8765   # or:  pe-mcp-web

BX reads finance_output/bx_report_<corpus>.html via file://, no server.

    # Output: /tmp/pe-demo/<hash>.webm

Convert to MP4 (one line printed at the end of the run):
    ffmpeg -y -i /tmp/pe-demo/<hash>.webm \
           -c:v libx264 -crf 20 -preset slow \
           /tmp/pe-demo/<hash>.mp4
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


BX_PROFILES: dict[str, BXProfile] = {
    "bx-procurement": BXProfile(
        name="bx-procurement",
        report_path=REPO / "finance_output" / "benchmark_D310_FY2024.html",
        regen_hint="python -c 'from finance_mcp.procurement import benchmark_vendors; benchmark_vendors(psc_code=\"D310\", fiscal_year=2024, max_records=500)'",
        intro_kicker="PROCUREMENT × CROSS-PORTCO",
        intro_text="Apollo's playbook, productized for the rest of mid-market PE.",
        intro_hint=(
            "198 real federal contracts · 28 agencies · 132 vendors · "
            "all USAspending.gov public data, no auth."
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
    ),
    "bx-plan-drift": BXProfile(
        name="bx-plan-drift",
        report_path=REPO / "finance_output" / "plan_drift_SoteraCo.html",
        regen_hint="python -c 'from finance_mcp.plan_drift import track_plan_drift; track_plan_drift(portco_id=\"SoteraCo\", ticker=\"SHC\")'",
        intro_kicker="100-DAY PLAN × DRIFT MONITOR",
        intro_text="The Day-60 problem caught before it becomes a QBR surprise.",
        intro_hint=(
            "7 frozen initiatives · diffed against real Sotera Health (SHC) "
            "10-Q actuals via SEC EDGAR."
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
    ),
    "bx-hmda-states": BXProfile(
        name="bx-hmda-states",
        report_path=REPO / "finance_output" / "bx_report_hmda_states.html",
        regen_hint="python -m scripts.build_bx_hmda_states",
        intro_kicker="BX · CROSS-PORTCO BENCHMARK",
        intro_text="A fund of 5 regional mortgage origination portcos.",
        intro_hint=(
            "DC · DE · MA · AZ · GA · all real CFPB HMDA 2023 · "
            "$184M fund-wide identifiable."
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
    ),
    "bx-mixed-fund": BXProfile(
        name="bx-mixed-fund",
        report_path=REPO / "finance_output" / "bx_report_mixed_fund.html",
        regen_hint="python -m scripts.build_bx_mixed_fund",
        intro_kicker="BX · MIXED-VERTICAL FUND",
        intro_text="7 portcos across consumer + mortgage lending.",
        intro_hint=(
            "5 Lending Club regional · 1 Yasserh mortgage · 1 HMDA DC · "
            "all real public data."
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
    ),
}


PROFILES: dict[str, Profile] = {
    "lending": Profile(
        name="lending",
        portco_id="LendingCo",
        loans=REPO / "demo" / "lending_club" / "loans.csv",
        perf=REPO / "demo" / "lending_club" / "performance.csv",
        regen_hint="python -m demo.lending_club.slice",
        intro_kicker="PRIVATE EQUITY × AI",
        intro_text="Decision-Optimization Diagnostic — runs locally.",
        intro_hint=(
            "Drop a portco's CSVs · pandas math · row-level evidence on "
            "every dollar."
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
        intro_kicker="PRIVATE EQUITY × AI",
        intro_text="DX on real CFPB HMDA data — Washington DC, 2023.",
        intro_hint=(
            "11.6k mortgage applications · public regulatory disclosure · "
            "no synthetic outcomes."
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
        intro_kicker="PRIVATE EQUITY × AI",
        intro_text="DX on a real US specialty-mortgage book.",
        intro_hint=(
            "148,670 originations · CC0 public data · same engine, "
            "different vertical."
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

        # ---- Scene 1: open /app/ — intro caption (~6s) ----
        page.goto(URL_APP, wait_until="networkidle")
        set_caption(
            page,
            profile.intro_kicker,
            profile.intro_text,
            profile.intro_hint,
        )
        time.sleep(6.0)

        # ---- Scene 2: type portco id (~4s) ----
        page.locator("#portcoId").click()
        page.locator("#portcoId").type(profile.portco_id, delay=130)
        set_caption(
            page,
            "STEP 1 · IDENTIFY THE PORTCO",
            "A label for this engagement — appears on the memo and the JSON sidecar.",
            "",
        )
        time.sleep(3.5)

        # ---- Scene 3: drag-drop the CSVs (~6s) ----
        set_caption(
            page,
            "STEP 2 · TWO ENTITIES",
            profile.entities_text,
            profile.entities_hint,
        )
        time.sleep(1.0)
        drop_files_visibly(page, "#dropZone", [profile.loans, profile.perf])
        time.sleep(4.0)

        # ---- Scene 4: click Run, show pipeline (~10s) ----
        page.locator("#runBtn").click()
        set_caption(
            page,
            "STEP 3 · PIPELINE — 7 STAGES",
            "ingest → segment-stats → time-stability → counterfactual → evidence → memo → report",
            "Pure pandas. No LLM does arithmetic. Every stage is a dx_* MCP tool.",
        )
        page.wait_for_selector(
            "#resultSummary:has-text('projected impact')", timeout=120_000
        )
        time.sleep(2.0)

        # ---- Scene 5: result summary (~5s) ----
        page.evaluate(
            "document.querySelector('#result').scrollIntoView({behavior:'smooth', block:'start'})"
        )
        set_caption(
            page,
            "STEP 4 · FINDING",
            profile.finding_text,
            profile.finding_hint,
        )
        time.sleep(5.5)

        # ---- Scene 6: drill into the top opportunity card (~6s) ----
        page.evaluate(
            "(() => {"
            "  const f=document.querySelector('#reportFrame');"
            "  if (!f) return;"
            "  const doc=f.contentDocument;"
            "  const opp=doc.querySelector('.opp');"
            "  if (opp) opp.scrollIntoView({behavior:'smooth', block:'start'});"
            "})()"
        )
        set_caption(
            page,
            "STEP 5 · EVERY OPPORTUNITY HAS",
            profile.opp_text,
            profile.opp_hint,
        )
        time.sleep(6.5)

        # ---- Scene 7: zoom into the memo prose (~7s) ----
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
            "Each opp ships with two narrative views — the why, the counterfactual, the rollout.",
            "Defensible language a managing director can read into a board meeting.",
        )
        time.sleep(7.0)

        # ---- Scene 8: closing (~5s) ----
        page.evaluate(
            "document.querySelector('.head').scrollIntoView({behavior:'smooth', block:'start'})"
        )
        set_caption(
            page,
            "OPEN SOURCE · MIT · PANDAS-DETERMINISTIC",
            "Two engines shipped: DX (decision diagnostic) + BX (cross-portco benchmark).",
            "Plus a 17-command deal-team workbench. github.com/bolnet/private-equity",
        )
        time.sleep(5.0)

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
