"""
Record a scripted walkthrough of the /diagnose-decisions upload UI on
real public data. Produces a .webm file that can be converted to MP4.

Two profiles ship — pick whichever you want to record:

    # 1. Make sure the server is running:
    python -m finance_mcp.web 8765   # or:  pe-mcp-web

    # 2. In another terminal:
    python scripts/record_demo.py --profile lending   # default
    python scripts/record_demo.py --profile yasserh

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
    """A demo profile — input CSVs + captions for the 8 scenes."""
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default="lending",
        help="Which demo profile to record (default: lending)",
    )
    args = parser.parse_args()
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
