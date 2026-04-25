# Private Equity × AI

> **A working toolkit for private-equity firms who want to turn AI from deck-slide into measurable portfolio value.**

Most "AI for PE" today is slides, vendor demos, and a 100-day plan template no one operationalizes. This is a shipped, runnable system that diagnoses where decisions are losing money inside a portco, benchmarks every portco against the rest of the fund, and feeds it back to the deal team and LPs in artifacts a partner can actually send.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Claude Code MCP](https://img.shields.io/badge/Claude%20Code-MCP%20server-purple)](https://docs.claude.com/en/docs/claude-code/mcp)

---

## The problem

**Private equity is two AI cycles behind its own best portcos.** The value gets left on the table at three layers:

| Layer | What's broken today | What ships here |
|---|---|---|
| **Deal team** | DD memos hand-assembled; market scans take days; no consistent prospect scoring | `/dd-checklist`, `/ic-memo`, `/score-prospect`, `/screen-deal`, `/public-comps`, `/market-risk` |
| **Portco operating** | Operating partners can't compare decisions across companies; AI roadmaps drift in a PDF | `/diagnose-decisions` (DX) — top 3 dollar-quantified decision opportunities from a portco's own data |
| **Fund / LP** | No view of which portcos share the same leakage pattern; LP letters can't quantify operational alpha | `/benchmark-corpus` (BX) — fund-wide rank, archetype index, quarterly persistence |

---

## What's in the box

### 1. Decision-Optimization Diagnostic (DX)
Pandas-only pipeline that ingests a portco's CSVs and surfaces the top 3 **repeatable, high-volume decisions being made badly** — each with a dollar-quantified counterfactual, time-stability score, and row-level evidence.

- `/diagnose-decisions` — ingest → segment-stats → time-stability → counterfactual → evidence → memo
- Output: HTML report + `OpportunityMap` JSON sidecar
- 3 vertical templates out of the box: `lending_b2c`, `saas_pricing`, `insurance_b2c`

### 2. Cross-Portco Benchmarking (BX)
Ingest N `OpportunityMap` JSONs from your portfolio and render an LP-grade benchmark.

- `/benchmark-corpus` — rank table, archetype index (pricing × selection × allocation), peer groups by cosine similarity
- `/benchmark-trend` — quarterly snapshots; closed / new / persistent deltas
- Output: static HTML the deal partner drops straight into a quarterly LP letter

### 3. AI-Readiness for PE
- `/ai-readiness` — scores a portco across data, ops, and governance dimensions; outputs a 90-day plan
- `/value-creation` — translates DX findings into a written value-creation plan
- `/portfolio` — fund-level dashboard rolling up all portco snapshots

### 4. The full skill library (17 skills, 18 slash commands)

| Category | Skills |
|---|---|
| **Sourcing & screening** | deal-sourcing, deal-screening, prospect-scoring, pipeline-profiling |
| **Diligence** | dd-checklist, dd-meeting-prep, public-comp-analysis, returns-analysis, unit-economics |
| **IC / decision** | ic-memo, market-risk-scan, liquidity-risk |
| **Portco operating** | decision-diagnostic, value-creation-plan, ai-readiness, portfolio-monitoring |
| **Fund-level** | benchmarking |

Every skill is reachable as a `/`-command in Claude Code; every command writes deterministic, auditable artifacts to `finance_output/`.

---

## Demo results — real public data

Numbers, not adjectives.

| Demo | Source | Lines | What it shows |
|---|---|---:|---|
| `lending_club` | Real Lending Club 2015–2016 30k-loan slice | ~60k | Single-portco DX surfaces the classic sub-prime refi pattern: **~$800k/yr identifiable** |
| `regional_lenders` | Same source partitioned into 5 US-region "portcos" | ~60k | Cross-portco BX shows **~$3.2M/yr fund-level identifiable impact**, with all three archetypes appearing in **5 / 5 portcos** — fund-wide themes, not anomalies |
| `etelequote` | Synthetic insurance B2C lead-routing | — | Reference shape for the `insurance_b2c` template |
| `saas_pricing` | Synthetic SaaS deal/discount data | — | Reference shape for the `saas_pricing` template |

```bash
# Single-portco DX on real Lending Club data
python -m demo.lending_club.slice
# In Claude Code: /diagnose-decisions on demo/lending_club/{loans,performance}.csv

# Five-portco fund-level BX corpus
python -m demo.regional_lenders.slice
python -m scripts.build_bx_corpus
# Renders finance_output/bx_report_regional_lenders_demo.html
```

---

## Install

```bash
git clone https://github.com/bolnet/private-equity.git
cd private-equity
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### As an MCP server (Claude Code)

```json
{
  "mcpServers": {
    "private-equity": {
      "command": "python",
      "args": ["-m", "finance_mcp.server"]
    }
  }
}
```

Then in Claude Code:
```
❯ /diagnose-decisions
Upload your portco's CSVs (e.g. loans.csv + performance.csv)…
```

### As a web upload UI

```bash
pe-mcp-web   # serves the upload UI at http://localhost:8765/app/
```

Drag a portco's CSV bundle in, get the HTML report and JSON sidecar back. No data leaves your machine.

---

## Who this is for

| Role | Where to start |
|---|---|
| **Operating partner** | `/diagnose-decisions` on one portco → 1-page memo for the board |
| **Deal team** | `/dd-checklist` + `/score-prospect` + `/public-comps` on a live target |
| **Fund principal / CIO** | `/benchmark-corpus` across all portcos → archetype rollup for the next IC |
| **LP-facing partner** | `/benchmark-trend` quarterly → operational-alpha exhibit in the LP letter |
| **Portco CEO / CFO** | `/ai-readiness` → 90-day plan grounded in *your* data, not a vendor pitch |

---

## Architecture

```
src/finance_mcp/
├── dx/                    # Decision-Optimization Diagnostic — pandas-only, deterministic
│   ├── ingest.py          # CSV → joined dataframe + template match
│   ├── segment_stats.py   # group-by + rank by $ outcome
│   ├── time_stability.py  # per-quarter persistence score
│   ├── counterfactual.py  # projected $ uplift if we re-route
│   ├── evidence.py        # row-level citations
│   ├── memo.py            # board + operator narratives
│   ├── report.py          # static HTML + JSON sidecar
│   └── templates.py       # 3 verticals: lending_b2c, saas_pricing, insurance_b2c
├── bx/                    # Cross-portco benchmarking
│   ├── ingest_corpus.py   # load N OpportunityMaps
│   ├── rank.py            # one portco's rank/percentile vs peers
│   ├── archetype_index.py # p10/median/p90 per archetype across the corpus
│   ├── peer_group.py      # cosine-similarity peer matching
│   ├── snapshot.py        # persist a dated snapshot for a single portco
│   ├── trend.py           # within-portco time-series
│   ├── delta.py           # closed / new / persistent between snapshots
│   └── report.py          # LP-facing static HTML
├── dx_orchestrator.py     # high-level run_diagnostic() driver
├── output.py              # finance_output/ conventions, headless matplotlib
├── server.py              # FastMCP entry point — registers DX + BX tools
└── web.py                 # Starlette upload UI for /diagnose-decisions
```

Design principles: **deterministic core** (pandas, not LLMs, for the math), **auditable output** (every dollar number traces to row-level evidence), **air-gappable** (no portco data leaves the operator's machine), **extensible** (a vertical template is one Python file).

---

## Roadmap

- More vertical templates: healthcare-services routing, industrial pricing, B2B SaaS retention
- Snowflake / BigQuery direct ingest (in addition to CSV)
- GP-portal mode: multi-tenant deployment so each portco only sees its own data; the GP sees the corpus
- Audit trail export: signed JSON bundle for LP / IC reproducibility

Issues and PRs welcome.

---

## Requirements

- Python 3.10+
- Claude Code CLI ([install](https://claude.com/claude-code))

## License

MIT. See [LICENSE](LICENSE).

## About

Built and maintained by [Surendra Singh](https://www.linkedin.com/in/surendrasingh/).
