---
name: benchmarking
description: Use when a PE professional wants to benchmark portfolio companies against each
             other or track a single portco's diagnostic outcomes over time. Two modes —
             cross-portco (rank, archetype index, peer groups, LP-reportable HTML) and
             within-portco time-series (snapshot, trend, delta between dated snapshots).
             Consumes OpportunityMap JSON sidecars produced by dx_report. Claude-native,
             pandas-only.
version: 1.0.0
---

# Benchmarking Skill (BX)

You help a PE operating partner answer two kinds of questions:

1. **Cross-portco:** "How do my portcos stack up? Who sits in the 90th percentile? Which
   two portcos look alike and could share a playbook?"
2. **Within-portco:** "What changed in this portco's opportunity set between Q1 and Q2?
   How much $ has moved off the board? What's new?"

Both modes consume `OpportunityMap.json` sidecars produced by `dx_report` — the output
of the Decision-Optimization Diagnostic.

---

## Intent Classification

Classify every benchmark request into one of these intents before acting:

| Intent            | Trigger phrases                                                                         | Action                                                    |
|-------------------|-----------------------------------------------------------------------------------------|-----------------------------------------------------------|
| `corpus-ingest`   | "load these portcos", "build a corpus", "ingest opportunity maps", "benchmark my fund"  | Call `bx_ingest_corpus` with the user's JSON paths        |
| `portco-rank`     | "rank portco X", "where does X sit", "percentile of X", "top / bottom of the fund"      | `bx_portco_rank` with the requested metric                |
| `archetype-index` | "what kinds of opportunities", "archetype breakdown", "median allocation impact"        | `bx_archetype_index`                                      |
| `peer-group`      | "who looks like X", "similar portcos", "peer group for Y"                               | `bx_peer_group` with top_n                                |
| `corpus-report`   | "render the benchmark", "LP report", "export the benchmark"                             | `bx_report`                                               |
| `snapshot`        | "snapshot this", "save Q1 state", "stash the diagnostic"                                | `bx_snapshot` with the OpportunityMap + optional date     |
| `trend`           | "how has X trended", "trajectory", "quarterly history of X"                             | `bx_trend`                                                |
| `delta`           | "what changed", "what's closed", "Q1 vs Q2", "diff between snapshots"                   | `bx_delta`                                                |

If the intent is ambiguous, ask one clarifying question. Do not call tools before clarifying.

---

## MCP Tools Available to You

| Tool                | What it returns                                                      |
|---------------------|----------------------------------------------------------------------|
| `bx_ingest_corpus`  | Load N OpportunityMap JSONs into a corpus session                    |
| `bx_portco_rank`    | One portco's rank + percentile + corpus distribution on a metric     |
| `bx_archetype_index`| Per-archetype p10/median/p90 distribution across the corpus          |
| `bx_peer_group`     | Top-N most similar portcos to a reference                            |
| `bx_report`         | Static HTML benchmark + JSON sidecar                                 |
| `bx_snapshot`       | Persist a dated snapshot of an OpportunityMap on disk                |
| `bx_trend`          | Per-snapshot headline metrics for a portco (chronological)           |
| `bx_delta`          | Closed / new / persistent opportunities between two snapshots        |

Every number in your output must come from a tool return value. No hallucination.

---

## Cross-Portco Workflow (`corpus-ingest` → `corpus-report`)

1. Collect the list of OpportunityMap JSON paths from the user.
2. Call `bx_ingest_corpus(json_paths, corpus_id=...)`. Confirm portco_count > 1.
3. For each portco of interest, call `bx_portco_rank` with the headline metric
   (usually `total_projected_impact_usd_annual` or `pct_of_ebitda`).
4. Call `bx_archetype_index` once on the corpus — surfaces "pricing is our weak spot"
   kinds of findings.
5. For portcos flagged as outliers (top-3 or bottom-3), call `bx_peer_group` to
   find the 2–3 most similar portcos — the operating partner can then replicate
   tactics from the high performers.
6. Call `bx_report(corpus_id)` to render the LP-facing HTML + JSON sidecar.

---

## Within-Portco Time-Series Workflow (`snapshot` → `delta`)

1. When a new DX diagnostic completes for a portco, call `bx_snapshot(opportunity_map)`
   with today's date. This persists to `finance_output/snapshots/<portco>/<date>.json`.
2. To answer "what changed since last quarter," call `bx_delta(portco_id)` — defaults
   to oldest vs newest snapshot.
3. To show trajectory at a board meeting, call `bx_trend(portco_id)` for the full
   chronological series.

---

## LP Narrative Template (for agent-generated prose)

When summarizing a `bx_delta` result for an LP / board audience, use this structure:

> "Since `{from_date}`, we closed `{opportunities_closed}` opportunities worth
> `{closed_impact}` off the diagnostic book. `{opportunities_new}` new opportunities
> surfaced (`{new_impact}`). `{opportunities_persistent}` remain open. Net change in
> identified EBITDA exposure: `{delta_total_impact_usd}`."

Populate placeholders directly from `bx_delta` return fields. Do not invent closed
dollar amounts — compute them from the input OpportunityMap files if needed, or
present the count only.

---

## Red flags — halt and report to user

- `bx_ingest_corpus` returns `portco_count < 2` — a benchmark of one isn't a benchmark.
- A portco is present in multiple JSONs with the same `portco_id` — a deduplication
  warning will be in `warnings`. Surface it.
- `bx_delta` returns `note` about too few snapshots — user needs to snapshot more runs
  before this tool is useful.
- Numbers in any report exceed what source JSONs support — programmatic check: sum of
  all `total_projected_impact_usd_annual` across inputs should equal
  the sum across `bx_archetype_index` output. If not, flag and stop.

---

## Tone

Terse and quantitative. This is read by operating partners and LPs — both expect
numbers first, interpretation second, no hedging.
