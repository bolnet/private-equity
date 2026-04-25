# OpportunityMap JSON Schema

The Decision-Optimization Diagnostic emits an `OpportunityMap` JSON sidecar next to every rendered HTML report. This document is the **contract** for that JSON — consume it with confidence from downstream LP reporting pipelines, CRM integrations, or dashboards.

- **File name pattern:** `finance_output/dx_report_<portco_id>.json`
- **Schema version:** `1.0`
- **Stability:** Additive changes only within `1.x`. Removed fields bump to `2.0`.

---

## Top-level object

| Field | Type | Required | Description |
|---|---|:-:|---|
| `portco_id` | string | ✅ | Free-form label for the portfolio company. Slug-safe. |
| `vertical` | string | ✅ | Template id used (`insurance_b2c`, `saas_pricing`, `custom`). |
| `ebitda_baseline_usd` | number | ✅ | Annualized baseline EBITDA before opportunities. May be negative. |
| `as_of` | string (ISO-8601 date) | ✅ | The diagnostic's reference date. |
| `opportunities` | array of Opportunity | ✅ | Ranked opportunities, highest impact first. |
| `total_projected_impact_usd_annual` | number | ✅ | Sum of `projected_impact_usd_annual` across all opportunities. |

### Example

```json
{
  "portco_id": "etelequote_demo",
  "vertical": "insurance_b2c",
  "ebitda_baseline_usd": -1200000.0,
  "as_of": "2026-04-24",
  "opportunities": [ ... ],
  "total_projected_impact_usd_annual": 9700000.0
}
```

---

## Opportunity object

One entry per detected blind-spot. **Ranked** by `projected_impact_usd_annual × persistence_score / difficulty_score`.

| Field | Type | Required | Description |
|---|---|:-:|---|
| `id` | string | ✅ | Stable identifier (e.g. `opp_TX_AffB_throttle`). Scoped per-report. |
| `archetype` | enum | ✅ | One of `allocation`, `pricing`, `routing`, `timing`, `selection`. |
| `decision_cols` | array of string | ✅ | The dataset columns being decided on (e.g. `["source", "state"]`). |
| `segment` | object<string, string\|number> | ✅ | Equality filter identifying the cell (e.g. `{"source": "Affiliate_B", "state": "TX"}`). |
| `n` | integer | ✅ | Row count in this segment. |
| `current_outcome_usd_annual` | number | ✅ | Annualized $ outcome under the status quo. May be negative. |
| `projected_outcome_usd_annual` | number | ✅ | Annualized $ outcome under the recommended action. |
| `projected_impact_usd_annual` | number | ✅ | `projected_outcome - current_outcome`, annualized. The headline figure. |
| `persistence_quarters_out_of_total` | `[int, int]` | ✅ | `[matching_quarters, observed_quarters]`. Used as evidence that the pattern is not transient. |
| `difficulty_score_1_to_5` | integer (1-5) | ✅ | Implementation difficulty. 1 = vendor dashboard toggle; 5 = multi-quarter replatform. |
| `time_to_implement_weeks` | integer | ✅ | Calendar weeks to implement. |
| `recommendation` | string | ✅ | One-line action, past-tense imperative ("Throttle ..."). |
| `evidence_row_ids` | array of integer | ✅ | Row IDs from the joined dataframe backing the claim. Sourced from `dx_evidence_rows`. |
| `narrative_board` | string | ⬜ | Board-audience memo prose. Empty → `dx_report` fills in a deterministic skeleton. |
| `narrative_operator` | string | ⬜ | Operator-audience memo prose. Same fallback behavior. |
| `quarters` | array of string | ⬜ | Quarter labels (`"2024Q3"`). Required together with the next field to render a chart. |
| `quarterly_outcome_total_usd` | array of number | ⬜ | Per-quarter $ totals aligned to `quarters`. |

### Example

```json
{
  "id": "opp_TX_AffB_throttle",
  "archetype": "allocation",
  "decision_cols": ["source", "state"],
  "segment": {"source": "Affiliate_B", "state": "TX"},
  "n": 1430,
  "current_outcome_usd_annual": -3800000.0,
  "projected_outcome_usd_annual": 0.0,
  "projected_impact_usd_annual": 3800000.0,
  "persistence_quarters_out_of_total": [12, 12],
  "difficulty_score_1_to_5": 1,
  "time_to_implement_weeks": 2,
  "recommendation": "Throttle TX × Affiliate_B lead buying to 3% of current volume",
  "evidence_row_ids": [56978, 79982, 59612, 21345, 55312],
  "narrative_board": "Over 36 months …",
  "narrative_operator": "Vendor dashboards support state-level caps …",
  "quarters": ["2023Q1","2023Q2","2023Q3","2023Q4","2024Q1","2024Q2","2024Q3","2024Q4","2025Q1","2025Q2","2025Q3","2025Q4"],
  "quarterly_outcome_total_usd": [-320000.0,-410000.0,-290000.0,-380000.0,-400000.0,-450000.0,-330000.0,-370000.0,-440000.0,-310000.0,-420000.0,-380000.0]
}
```

---

## Consumer guidelines

### LP reporting pipelines

- Treat `total_projected_impact_usd_annual` as the headline number; treat `ebitda_baseline_usd` as context.
- For trend tracking across quarters, read `persistence_quarters_out_of_total` to filter out transient findings.
- `evidence_row_ids` are stable only within a single diagnostic run. Do not use them as cross-report keys.

### CRM / task systems

- Use `id` as a stable reference within one run.
- Push `recommendation` + `time_to_implement_weeks` directly to ticket titles.
- Map `difficulty_score_1_to_5` to your own priority / T-shirt sizing scheme.

### Dashboards

- Sort the array by `projected_impact_usd_annual` descending to display.
- Use `persistence_quarters_out_of_total[0] / [1]` as a confidence indicator badge.
- Negative `current_outcome_usd_annual` combined with positive `projected_impact_usd_annual` is the "recoverable loss" case — highlight it.

---

## Schema versioning

| Version | Changes |
|---|---|
| `1.0` | Initial schema. |

Breaking changes (field removal, type change, enum narrowing) will bump to `2.0` and the prior schema will be maintained in parallel for at least one milestone.

---

## Field derivation (for implementors)

| Field | Derived from tool |
|---|---|
| `n` | `dx_segment_stats.segments[i].n` or `dx_evidence_rows.total_matched` |
| `current_outcome_usd_annual` | `dx_counterfactual.current_outcome_usd_annual` |
| `projected_outcome_usd_annual` | `dx_counterfactual.projected_outcome_usd_annual` |
| `projected_impact_usd_annual` | `dx_counterfactual.projected_impact_usd_annual` |
| `persistence_quarters_out_of_total` | `[dx_time_stability.persistence_quarters, .total_quarters]` |
| `evidence_row_ids` | `[r.row_id for r in dx_evidence_rows.evidence_rows]` |
| `quarters`, `quarterly_outcome_total_usd` | `dx_time_stability.quarters`, `.quarterly_outcome_total_usd` |

Every numeric field traces to a tool-return value. This is the product invariant: **no number in the JSON may be invented**.
