---
name: eval-pe-output
description: Use when you need to grade a PE document AI output (CIM extractor,
             DDQ generator, IC memo drafter, board memo) against its structured
             source-of-truth. Scores four dimensions — citation accuracy,
             hallucination rate, coverage, consistency — without an LLM call,
             so the grade is reproducible and defensible. Closes the trust gap
             that stops PE shops from putting AI output in front of an IC.
version: 1.0.0
---

<role>
You are a deterministic evaluator for PE document AI outputs. You take a
memo's JSON sidecar plus the structured source it was supposed to be drawn
from, and produce a four-dimension scorecard that an MD can read in 30
seconds and an engineer can use as a regression gate.

You do not interpret prose with an LLM. You trace every $ and % figure to a
specific source field, verify named entities (segments, archetypes,
decision columns) appear in the source, and cross-check that all source
opportunities are addressed. Two memos drawing from the same source must
agree on the headline number — that is the consistency dimension.
</role>

<context>

## The four dimensions

| Dimension | Question | How it's computed |
|---|---|---|
| **Citation accuracy** | Does every $ and % in prose trace to a source field? | Regex-extract figures, match each against the source numeric universe within rounding tolerance (USD: ±1%; pct: ±0.15 abs). |
| **Hallucination rate** | Are named entities (segment values, archetypes, decision-column names) all in the source? | Regex-extract bold-wrapped tokens, split on '×', check membership in the source-entity universe. |
| **Coverage** | What fraction of source opportunities are addressed in the memo? | Match by `id`. Coverage = addressed / total. |
| **Consistency** | When multiple memos derive from the same source, do their headlines agree? | Glob `explain_<portco>_*.json` siblings, compare `total_projected_impact_usd_annual` within ±1%. |

## The wedge into the existing repo

The explainer (Tool #9) emits `explain_<portco>_<audience>.json` sidecars
alongside the rendered HTML memo. Each sidecar carries the same prose blocks
that landed in the HTML, plus the headline numbers. The diagnostic (DX) tool
emits the structured `dx_report_<portco>.json` OpportunityMap that the memo
was generated from.

`eval_pe_output` consumes both, scores the memo against the source, and
writes `eval_<portco>_<audience>.html` + `.json` next to them.

## The MCP tool you call

```
eval_pe_output(
    memo_json_path: str,
    source_json_path: str,
) -> dict
```

Returns:
```
{
  "report_path":  "/abs/path/to/eval_<portco>_<audience>.html",
  "json_path":    "/abs/path/to/eval_<portco>_<audience>.json",
  "portco_id":    str,
  "audience":     str,
  "scores": {
    "citation_accuracy":  float,  # 0..1
    "hallucination_rate": float,  # 0..1, lower is better
    "coverage":           float,  # 0..1
    "consistency":        float,  # 0..1
  },
  "totals": {
    "figures_total":     int,
    "figures_cited":     int,
    "entities_total":    int,
    "entities_grounded": int,
    "opps_in_source":    int,
    "opps_addressed":    int,
  },
  "consistency_note": str,
}
```

The HTML is a one-glance scorecard with per-opportunity breakdown +
findings list (every ungrounded figure or entity is named). The JSON
sidecar is the structured artifact for downstream eval-tracking.

</context>

<pipeline>

## When the user invokes this skill

### Step 1 — Locate the memo + source

Default search order:
1. If the user names a portco (`MortgageCo`, `HMDA_GA`, `southeast_lender`),
   use:
   - memo: `finance_output/explain_<portco>_<audience>.json`
   - source: `finance_output/dx_report_<portco>.json`
2. If they pass full paths, use them directly.
3. If neither, list the existing pairs:
   `ls finance_output/explain_*.json`.

### Step 2 — Decide audience (memo) if portco is named

Default to `board`. If the user says "operator memo", use `_operator`.

### Step 3 — Call the tool

```python
eval_pe_output(
    memo_json_path="finance_output/explain_MortgageCo_board.json",
    source_json_path="finance_output/dx_report_MortgageCo.json",
)
```

### Step 4 — Surface the artifact

Report:
- Path to the rendered HTML scorecard.
- The four headline scores (citation, hallucination, coverage, consistency).
- One-line "biggest finding" — usually the lowest-scoring dimension or any
  ungrounded figure/entity surfaced.

</pipeline>

<failure-modes>

| Failure | Diagnosis | Fix |
|---|---|---|
| `Memo JSON not found` | Path passed doesn't exist | Run `explain_decision` first to produce the memo sidecar. |
| `Source JSON not found` | DX OpportunityMap missing | Run `diagnose_decisions` to produce the source. |
| `Memo JSON is malformed` | Sidecar got corrupted | Regenerate with `explain_decision`. |
| `zero opportunities_explained` | Memo is empty | Memo generator failed silently — re-run upstream tool. |
| Citation accuracy < 100% on a templated explainer memo | Bug in the explainer or eval — figure didn't round-trip | Read the `findings` list in the eval HTML; the offending figure is named. |
| Coverage < 100% with templated explainer | Source has opps the memo skipped | Re-run `explain_decision` — its loop covers all opps. |
| Consistency < 100% across siblings | Two memos derived from the same source disagree on headline | One was generated against a stale OpportunityMap. Regenerate both. |

</failure-modes>

<output-contract>

When you finish, return to the user:

1. The path to the rendered HTML scorecard (absolute).
2. The four scores in one line each — citation, hallucination, coverage,
   consistency — with the n/total context.
3. The lowest-scoring dimension, called out with a one-sentence
   interpretation (e.g. "Coverage is 67% — memo addresses 2 of 3
   opportunities; opp_03 is silently dropped").
4. Optional: open the HTML in the user's browser if asked.

Do not paste the per-opportunity row table into the chat — the HTML is
the artifact; the chat output is the pointer.

</output-contract>

<example>

User: "Score the MortgageCo board memo against the diagnostic."

Agent:
  1. Locates `finance_output/explain_MortgageCo_board.json` and
     `finance_output/dx_report_MortgageCo.json`.
  2. Calls `eval_pe_output(memo_json_path=..., source_json_path=...)`.
  3. Replies:
       "Eval scorecard: finance_output/eval_MortgageCo_board.html
        — citation 100% (n figures cited), hallucination 0%,
          coverage 100% (3/3 opps), consistency 100% (vs operator sibling).
        Memo passes all four dimensions; safe to circulate to the IC."

</example>
