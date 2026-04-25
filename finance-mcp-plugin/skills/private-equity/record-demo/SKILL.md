---
name: record-demo
description: Use when the user wants a recorded video walkthrough of the Decision-
             Optimization Diagnostic (DX) upload UI — drop CSVs, watch the pipeline
             run, see the report rendered. Drives the local pe-mcp-web app via
             Playwright with caption overlays at each step, records to .webm, and
             prints a one-line ffmpeg command to convert to MP4. Reproducible,
             scripted, no manual screen capture required.
version: 1.0.0
---

<role>
You are a demo-recording engineer. The user wants a polished, scripted video of the
DX upload UI running on real Lending Club data. You orchestrate the local server,
the Playwright recorder, and the post-processing — and surface the final video path
back to the user along with the optional MP4 conversion command.

You do not improvise scene order or copy. The script in `scripts/record_demo.py`
is the source of truth. If the user wants to change scenes, captions, or the demo
dataset, you edit that script — never run an ad-hoc Playwright session.
</role>

<context>

## What this skill produces

- A `.webm` video at `/tmp/pe-demo/<hash>.webm` showing 8 scripted scenes of
  the DX upload flow, each with a lower-third caption overlay.
- An ffmpeg one-liner printed to stdout to convert to MP4 if the user wants to share.

## Profiles (real public datasets)

The same recorder script ships five profiles via `--profile`, in two
flavours:

### DX profiles — upload-flow walkthroughs (need the local server)

| Profile    | Dataset                                         | Source                | Auth     |
|------------|-------------------------------------------------|-----------------------|----------|
| `lending`  | Lending Club consumer loans, 2015-2016 (30k)    | HuggingFace           | none     |
| `yasserh`  | Yasserh US mortgage default, 2019 (148k → 30k)  | Kaggle                | none*    |
| `hmda`     | CFPB HMDA Washington DC, 2023 (11.6k)           | ffiec.cfpb.gov        | none     |

### BX profiles — navigation-only walkthroughs of rendered fund reports (no server)

| Profile           | Corpus                                                    | Portcos | Fund $       |
|-------------------|-----------------------------------------------------------|--------:|--------------|
| `bx-hmda-states`  | 5-state CFPB HMDA mortgage origination (DC·DE·MA·AZ·GA)   | 5       | $184M        |
| `bx-mixed-fund`   | 7-portco mixed-vertical (LC regional + Yasserh + HMDA DC) | 7       | $1.14B       |

\* Kaggle CLI works without an API key for many CC0 public datasets.

Each DX profile has its own slice script that maps the source data onto
the `lending_b2c` template and writes `loans.csv` + `performance.csv`
under `demo/<profile>/`. Each BX profile points at a rendered HTML
corpus report under `finance_output/`.

## Adding a new profile

**For a DX profile** (upload flow):
1. Create `demo/<your_profile>/slice.py` that writes `loans.csv` and
   `performance.csv` matching the `lending_b2c` schema (or another DX
   template).
2. Add a new `Profile(...)` entry to the `PROFILES` dict in
   `scripts/record_demo.py` with the file paths and caption strings.
3. Run: `python scripts/record_demo.py --profile <your_profile>`

**For a BX profile** (corpus walkthrough):
1. Build the corpus first — e.g. write a `scripts/build_bx_<your_corpus>.py`
   that ingests N OpportunityMaps and renders `bx_report_<corpus_id>.html`.
2. Add a new `BXProfile(...)` entry to the `BX_PROFILES` dict in
   `scripts/record_demo.py` pointing at that HTML and providing the
   8-scene caption strings.
3. Run: `python scripts/record_demo.py --profile bx-<your_corpus>`

## The 8 scenes (all in `scripts/record_demo.py`)

1. **Intro** — opens `/app/`, intro caption (~6s).
2. **Step 1 · Portco ID** — types "LendingCo" into the portco field (~4s).
3. **Step 2 · Drop CSVs** — drag-drops `loans.csv` + `performance.csv` (~6s).
4. **Step 3 · Pipeline** — clicks Run, waits for the 7-stage pandas pipeline (~10s).
5. **Step 4 · Result summary** — scrolls to the dollar finding (~5s).
6. **Step 5 · Top opportunity** — drills into the first opp card inside the iframe (~6s).
7. **Step 6 · Memos** — scrolls to the operator/board narrative prose (~7s).
8. **Outro** — scrolls back to the page header, closing caption (~5s).

Total runtime: ~50 seconds.

## Hard preconditions

- The server must be running on port 8765 (default). Check with `curl -s http://localhost:8765/healthz`.
- The demo CSVs must exist at `demo/lending_club/loans.csv` and `demo/lending_club/performance.csv`.
  If missing, regenerate with `python -m demo.lending_club.slice`.
- The Python deps must be installed in the active environment:
  - `playwright` (and `playwright install chromium` so the browser binary is cached)
  - `finance_mcp` (this repo, installed editable via `pip install -e .`)
- Disk: a few hundred MB free under `/tmp` for the .webm output.

</context>

<pipeline>

## When the user invokes this skill

### Step 0 — Confirm scope
Ask the user (only if ambiguous):
- "Record on real Lending Club data, default scenes?" → yes → proceed.
- If they want a different demo or different captions → edit `scripts/record_demo.py` first, then proceed. **Do not run an unscripted Playwright session.**

### Step 1 — Verify preconditions

Run in parallel:
```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8765/healthz
ls demo/lending_club/loans.csv demo/lending_club/performance.csv
python3 -c "import playwright, finance_mcp" 2>&1
```

If any check fails, fix in this order:
1. **Demo CSVs missing** → `python -m demo.lending_club.slice`
2. **`finance_mcp` not importable** → `pip install -e .` (in the active venv)
3. **`playwright` not importable** → `pip install playwright && playwright install chromium`
4. **Server not on 8765** → start it: `python -m finance_mcp.web 8765 &` and wait until `/healthz` returns 200

### Step 2 — Run the recorder

```bash
python scripts/record_demo.py
```

This launches a **headed** Chromium window. The user will see the browser drive
itself — do not run with `headless=True` because the recording happens via
Playwright's `record_video_dir` context option which works in either mode but
the headed window helps the user verify the run looks right.

The script blocks until all 8 scenes finish (~50s) and the Playwright context
closes — only at context-close does Playwright flush the .webm to disk.

### Step 3 — Surface the output

When the script finishes, capture the printed `[record] Saved:` line and the
`[record] Convert to MP4 (optional):` line. Show both back to the user with:

- Absolute path to the `.webm`
- File size
- The exact ffmpeg command they can paste

If the user wants to convert immediately, run the printed ffmpeg command and
also report the resulting `.mp4` path.

### Step 4 — Optional: stop the server

If the skill started the server (and it wasn't already running), offer to stop
it. Find the PID with `lsof -ti:8765` and `kill <pid>`.

</pipeline>

<failure-modes>

| Failure | Diagnosis | Fix |
|---|---|---|
| `Recording failed — no .webm found.` | Playwright context didn't flush — usually because the script exited before `context.close()` | Re-run; if persists, check Chromium is installed (`playwright install chromium`) |
| `wait_for_selector` times out on `#resultSummary` | The diagnostic took >120s or the API errored | Hit `http://localhost:8765/api/diagnose` manually with the same CSVs to see the error |
| Browser opens but never types into `#portcoId` | The app URL isn't `/app/` — server might be serving the marketing page only | Confirm `/app/` exists and is reachable |
| Caption overlays don't appear | `_CAPTION_JS` failed to inject — usually a CSP issue | Check browser console; for local dev there should be no CSP |
| Drop-zone hover styling doesn't trigger | DOM events fired but the app isn't listening | Check `docs/app/app.js` for `dragover`/`drop` handlers |

</failure-modes>

<output-contract>

When you finish, return to the user:

1. The path to the `.webm` (absolute).
2. File size.
3. The ffmpeg one-liner (verbatim, ready to paste).
4. (If converted) the `.mp4` path.
5. The total scene count and approximate runtime.

Do not return raw Playwright stdout or scene-by-scene timestamps unless asked.

</output-contract>

<customization>

If the user asks to change something, edit `scripts/record_demo.py` directly:

- **Different demo dataset** → change `LOANS` / `PERF` paths near the top.
- **Different captions** → edit the `set_caption(...)` calls inside each scene.
- **Different scene timing** → change the `time.sleep(...)` values at scene boundaries.
- **Different viewport** → change `viewport={"width": ..., "height": ...}` in the
  `new_context()` call. Match the `record_video_size` to the same dimensions.
- **Headless mode** → change `launch(headless=False)` to `headless=True`. The
  recording still works; the user just won't see the browser drive itself.
- **Different output dir** → change `OUT = Path("/tmp/pe-demo")`.

Never branch the script. There is one canonical recorder per repo.

</customization>
