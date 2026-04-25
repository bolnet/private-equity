# Private-Equity MCP

> **Decision-Optimization Diagnostic + Cross-Portco Benchmarking — for Claude Code.**

A focused MCP server for private-equity workflows: ingest a portfolio company's
operational data and identify the top repeatable, high-volume decisions being
made badly (DX), then benchmark every portco against its fund peers (BX).

Extracted from the broader [bolnet/claude-finance](https://github.com/bolnet/claude-finance)
repo so PE-specific code can ship and version independently.

---

## What you get

- **Decision-Optimization Diagnostic (DX)** — `/diagnose-decisions` runs an
  ingest → segment-stats → time-stability → counterfactual → evidence → memo
  pipeline on a portco's CSV uploads. Produces an HTML report with up to 3
  ranked, dollar-quantified decision opportunities + a JSON `OpportunityMap`
  sidecar.
- **Cross-Portco Benchmarking (BX)** — `/benchmark-corpus` ingests N
  `OpportunityMap` JSONs into a corpus and renders an LP-facing benchmark
  with rank table, archetype index, and peer groups.
- **Time-series benchmarking** — `/benchmark-trend` snapshots quarterly
  diagnostic runs and surfaces what was closed / new / persistent between
  any two snapshots for a single portco.
- **17 PE skills** — DD checklist, IC memo drafting, deal screening,
  prospect scoring, public-comp analysis, returns analysis, unit
  economics, value-creation plans, and more.
- **18 slash commands** — every skill is reachable as a `/`-command.

## Demos (real public data, no synthetic generation)

| Demo                | What it is                                              | Lines |
|---------------------|---------------------------------------------------------|------:|
| `lending_club`      | Real Lending Club 2015–2016 30k-loan slice (single portco)  |  60k |
| `regional_lenders`  | The same source partitioned into 5 US-region "portcos" for BX | 60k |
| `etelequote`        | Synthetic insurance B2C lead-routing (reference shape)  |    — |
| `saas_pricing`      | Synthetic SaaS deal/discount data (reference shape)     |    — |

The DX `lending_b2c` template surfaces the classic sub-prime refi pattern
(~$800k/yr identifiable on the single-portco demo). The BX corpus across the
five regional portcos shows ~$3.2M/yr of fund-level identifiable impact, with
**all three archetypes** (`pricing` × `selection` × `allocation`) appearing
in 5 / 5 portcos — i.e. fund-wide themes, not one-off anomalies.

```bash
# Single-portco DX
python -m demo.lending_club.slice
# Then: /diagnose-decisions on demo/lending_club/{loans,performance}.csv

# 5-portco BX corpus
python -m demo.regional_lenders.slice
python -m scripts.build_bx_corpus
# Renders finance_output/bx_report_regional_lenders_demo.html
```

## Install

```bash
git clone https://github.com/bolnet/private-equity.git
cd private-equity
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Use it

### As an MCP server (Claude Code)

Add to `.mcp.json`:

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

## Architecture

```
src/finance_mcp/
├── dx/                    # Decision-Optimization Diagnostic — pandas-only
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

## Requirements

- Python 3.10+
- Claude Code CLI

## License

MIT — see the upstream [bolnet/claude-finance](https://github.com/bolnet/claude-finance) repo for license terms (this is a focused subset).

## About

Built by [Surendra Singh](https://www.linkedin.com/in/surendrasingh/). The PE
module of the broader Claude Finance project, lifted out so private-equity
operating partners can install it without the rest of the finance stack.
