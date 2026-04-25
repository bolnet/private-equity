"""
Record a scripted walkthrough of the /diagnose-decisions upload UI with
real Lending Club data. Produces a .webm file that can be converted to
MP4 with ffmpeg.

Usage:
    # Make sure the server is running first:
    python -m finance_mcp.web 8765

    # Then:
    python scripts/record_demo.py
    # Output: /tmp/claude-finance-demo/<hash>.webm
"""
from __future__ import annotations

import base64
import sys
import time
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
        z-index: 999999; max-width: 820px; width: calc(100% - 48px);
        pointer-events: none; font-family: 'DM Sans', -apple-system, sans-serif;
      }
      #__demo_caption_box {
        background: rgba(10, 10, 11, 0.94);
        border: 1px solid rgba(0, 212, 170, 0.4);
        border-radius: 14px;
        padding: 14px 22px;
        color: #f0f0f2;
        box-shadow: 0 16px 48px rgba(0,0,0,0.45), 0 0 0 1px rgba(0,212,170,0.08);
        backdrop-filter: blur(14px);
        opacity: 0; transform: translateY(18px);
        transition: opacity .42s ease, transform .42s ease;
      }
      #__demo_caption_box.show { opacity: 1; transform: translateY(0); }
      #__demo_caption_kicker {
        font-family: 'JetBrains Mono', monospace;
        font-size: 10.5px; letter-spacing: 0.18em; text-transform: uppercase;
        color: #00d4aa; margin-bottom: 6px;
      }
      #__demo_caption_text {
        font-size: 17px; line-height: 1.45; color: #f0f0f2; font-weight: 500;
      }
      #__demo_caption_hint {
        font-size: 13.5px; color: #94949e; margin-top: 6px; line-height: 1.45;
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
    this actually fires dragenter/dragover/drop DOM events, which triggers
    the drop zone's :hover styling and ondrop handler, so the animation is
    visible on camera.
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

            // dragenter + dragover paint the accent-dim hover state
            zone.dispatchEvent(new DragEvent('dragenter', { bubbles: true, dataTransfer: dt }));
            zone.dispatchEvent(new DragEvent('dragover',  { bubbles: true, dataTransfer: dt }));
            await new Promise(r => setTimeout(r, 900));   // hover hold — visible on video
            zone.dispatchEvent(new DragEvent('drop',      { bubbles: true, dataTransfer: dt }));
        }""",
        {"files": payload, "selector": zone_selector},
    )

URL = "http://127.0.0.1:8765/"
REPO = Path(__file__).resolve().parent.parent
LOANS = REPO / "demo" / "lending_club" / "loans.csv"
PERF = REPO / "demo" / "lending_club" / "performance.csv"
OUT = Path("/tmp/claude-finance-demo")
OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    if not LOANS.exists() or not PERF.exists():
        sys.exit(
            f"Demo CSVs missing. Regenerate with: "
            f"python -m demo.lending_club.slice\n"
            f"Looking for: {LOANS} + {PERF}"
        )

    with sync_playwright() as p:
        # Headed so the browser window is visible while the recording runs —
        # useful when the user wants to also screen-capture the whole OS or
        # verify the walkthrough visually before shipping.
        browser = p.chromium.launch(headless=False, slow_mo=150)
        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            record_video_dir=str(OUT),
            record_video_size={"width": 1400, "height": 900},
        )
        page = context.new_page()

        # ---- Scene 1: hero + intro caption (~7s) ----
        page.goto(URL, wait_until="networkidle")
        set_caption(
            page,
            "CLAUDE FINANCE",
            "Institutional analytics in plain English.",
            "40+ MCP tools · 21 slash commands · 15 PE workflows · open source.",
        )
        time.sleep(6.5)

        # ---- Scene 2: scope to PE (~6s) ----
        page.evaluate(
            "document.querySelector('#commands').scrollIntoView({behavior:'smooth', block:'start'})"
        )
        set_caption(
            page,
            "TODAY'S FOCUS",
            "Private Equity — 15 workflows for sourcing, DD, monitoring, and value creation.",
            "We'll demo one module: the Decision-Optimization Diagnostic.",
        )
        time.sleep(6.0)

        # ---- Scene 3: zoom on DX module card (~6s) ----
        page.evaluate(
            "document.querySelector('#modules').scrollIntoView({behavior:'smooth', block:'start'})"
        )
        set_caption(
            page,
            "DX · DECISION-OPTIMIZATION DIAGNOSTIC",
            "Find cross-section failures that aggregate dashboards hide.",
            "Output: a board-ready opportunity map with modeled EBITDA uplift per decision.",
        )
        time.sleep(6.0)

        # ---- Scene 4: click Try it → /app/ (~3s) ----
        page.locator(".module-try").first.click()
        page.wait_for_url("**/app/")
        set_caption(
            page,
            "RUNS LOCALLY",
            "Drop your portfolio company's CSVs. Nothing leaves localhost.",
            "Real demo data: 30,000 Lending Club loans, 2015–2016 vintage.",
        )
        time.sleep(3.0)

        # ---- Scene 5: type portco + drag-and-drop (~7s) ----
        page.locator("#portcoId").click()
        page.locator("#portcoId").type("LendingCo", delay=130)
        time.sleep(1.0)
        set_caption(
            page,
            "TWO ENTITIES",
            "loans.csv (underwriting) + performance.csv (servicing) — joined on loan_id.",
            "Auto-matched to the lending_b2c template.",
        )
        drop_files_visibly(page, "#dropZone", [LOANS, PERF])
        time.sleep(3.5)

        # ---- Scene 6: pipeline animates (~5s) ----
        page.locator("#runBtn").click()
        set_caption(
            page,
            "PIPELINE — 7 STAGES",
            "ingest → segment-stats → time-stability → counterfactual → evidence → memo → report",
            "Pure pandas. No black boxes. Every stage is a dx_* MCP tool.",
        )
        page.wait_for_selector(
            "#resultSummary:has-text('projected impact')", timeout=60_000
        )
        time.sleep(2.0)

        # ---- Scene 7: result summary (~5s) ----
        page.evaluate(
            "document.querySelector('#result').scrollIntoView({behavior:'smooth', block:'start'})"
        )
        set_caption(
            page,
            "FINDING",
            "$796k/yr identified · 5.5% of EBITDA · 5 opportunities ranked.",
            "All on real Lending Club originations — not synthetic.",
        )
        time.sleep(5.5)

        # ---- Scene 8: drill into the opportunity card (top-3 list) (~6s) ----
        page.evaluate(
            "(() => {"
            "  const f=document.querySelector('#reportFrame');"
            "  if (!f) return;"
            "  const doc=f.contentDocument;"
            "  const opps=doc.querySelector('.opp');"
            "  if (opps) opps.scrollIntoView({behavior:'smooth', block:'start'});"
            "})()"
        )
        set_caption(
            page,
            "EVERY OPPORTUNITY HAS",
            "Segment definition · annualized $ impact · persistence quarters · counterfactual.",
            "Sub-prime grades × refi loans persistently lose money — quantified.",
        )
        time.sleep(6.5)

        # ---- Scene 9: zoom into the memo prose (~7s) ----
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
            "BOARD + OPERATOR MEMOS",
            "Each opp ships with two narrative views — the why, the counterfactual, the rollout.",
            "Defensible language a managing director can read into a board meeting.",
        )
        time.sleep(7.0)

        # ---- Scene 10: closing + roadmap (~5s) ----
        page.evaluate(
            "document.querySelector('.head').scrollIntoView({behavior:'smooth', block:'start'})"
        )
        set_caption(
            page,
            "PHASE 1 ROADMAP",
            "BX cross-portco benchmarking · live SSE streaming · custom templates.",
            "Ship the diagnostic → ship the next decision. Open source, MIT.",
        )
        time.sleep(5.0)

        video_path = page.video.path() if page.video else None
        context.close()
        browser.close()

    # After context.close(), the video is flushed to disk under OUT/.
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
