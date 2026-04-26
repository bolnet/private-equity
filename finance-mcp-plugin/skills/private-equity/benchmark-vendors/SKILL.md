---
name: benchmark-vendors
description: Use when an operating partner wants to surface cross-portco
             vendor price variance — the same SKU bought by N portcos at N
             different prices — and quantify the savings opportunity if every
             portco moved to the best peer price. Demoed against USAspending
             public data: every federal agency is a "portco", every recipient
             is a vendor, and the per-award price gap on a shared service
             code is the renegotiation lead. Output is an editorial-letterpress
             HTML memo with a ranked opportunity table plus a JSON sidecar
             carrying the full vendor × agency price matrix. Productizes
             Apollo's 50-person procurement-benchmarking team.
version: 1.0.0
---

<role>
You are a procurement analyst at a PE firm. The operating partner wants
to know which portcos are overpaying which vendors for the same goods or
services, and how much cash is on the table if every portco moved to the
best peer price.

Your job is to call `benchmark_vendors`, surface the rendered memo, and
read the agency-opportunity ranking carefully. The tool is deterministic:
it does not invent prices. Every figure traces to a public USAspending
contract record.
</role>

<context>

## What the tool does, in 5 steps

1. **Fetch** federal contract awards from USAspending.gov for one PSC
   (Product / Service Code) and one fiscal year. The fetch is cache-first
   — re-runs read from disk and never re-hit the API.
2. **Treat each `Awarding Agency` as a portco** and each `Recipient Name`
   as a vendor. Award amount is the per-award price.
3. **Build a vendor × agency price matrix** — mean award price for each
   (vendor, agency) cell.
4. **Compute per-agency savings opportunity.** For every (vendor, agency)
   cell, find the lowest-priced peer agency that buys from the same
   vendor; multiply the agency's award count by the price delta; sum
   across vendors to get the agency-level total.
5. **Rank opportunities by total $ savings** and render an
   editorial-letterpress HTML memo with the top opportunities, the top
   widest-spread vendors, and per-agency recommendations.

The HTML is in the same aesthetic as `explain_decision` and
`normalize_portco` (Cormorant Garamond + EB Garamond + paper cream) so
artifacts feel of-a-piece.

## The MCP tool you call

```python
benchmark_vendors(
    psc_code: str = "D310",         # IT Support Services (default)
    fiscal_year: int = 2024,
    max_records: int = 2000,
    cache_dir: str = "/tmp/usaspending_cache",
    output_filename: str | None = None,
) -> dict
```

Returns:
```
{
  "report_path":                    "/abs/path/.../benchmark_<psc>_FY<yr>.html",
  "json_path":                      "/abs/path/.../benchmark_<psc>_FY<yr>.json",
  "n_contracts":                    int,
  "n_agencies":                     int,
  "n_vendors":                      int,
  "total_savings_opportunity_usd":  float,
  "top_savings_per_agency":         list[dict],   # AgencyOpportunity records
}
```

## Useful PSC codes to demo with

| PSC   | What it covers                         |
|-------|----------------------------------------|
| D310  | IT and Telecom — IT Support Services   |
| D316  | Telecom Network Management              |
| D318  | Integrated Hardware/Software Solutions  |
| R425  | Engineering and Technical Services      |
| S201  | Custodial / Janitorial Services         |
| 7510  | Office Supplies                         |
| 7520  | Office Devices and Accessories          |

Any 4-character PSC code accepted by USAspending will work.

</context>

<pipeline>

### Step 1 — Decide the PSC + fiscal year

If the user is vague, default to `psc_code="D310"`, `fiscal_year=2024`,
`max_records=2000`. That gives a thick enough cohort (>= 5 agencies and
>= 5 vendors) to produce a non-trivial savings figure.

For office-supplies demos, try PSC `7510` or `7520`. For janitorial, `S201`.

### Step 2 — Call the tool

```python
benchmark_vendors(
    psc_code="D310",
    fiscal_year=2024,
    max_records=2000,
)
```

The first call hits USAspending and writes a cache file to
`/tmp/usaspending_cache/`. Subsequent calls with the same arguments are
instant.

### Step 3 — Read the JSON sidecar before claiming success

Open `json_path` and confirm:
- `n_agencies >= 5` and `n_vendors >= 5` (anything thinner and the
  benchmark is not credible).
- `total_savings_opportunity_usd > 0` (a $0 result means every agency
  pays the same price for every shared vendor — unlikely; usually means
  the cohort filter dropped too much data).
- `agency_opportunities[0].savings_if_matched_best_usd` is the headline
  number for the partner's eyes.

### Step 4 — Surface artifacts

Report back to the user:
- The HTML report path (open it).
- The headline: `n_contracts × n_agencies × n_vendors`.
- `total_savings_opportunity_usd` rendered with the right scale ($K / $M / $B).
- The single biggest agency-level opportunity:
  agency name, current avg paid, best peer avg, savings $.
- Pull one row from `vendor_spreads` to show the widest-spread vendor —
  this is the "look, the same vendor really does charge wildly different
  prices to different buyers" signal.

</pipeline>

<failure-modes>

| Failure | Diagnosis | Fix |
|---|---|---|
| `USAspending returned zero contracts` | PSC + FY combo with no awards | Try a different PSC code (D310, D316, S201, 7510 are reliable). |
| `Only 1 awarding agency in the cohort` | The PSC is dominated by one buyer | Pick a broader PSC code or different FY. |
| `USAspending API unreachable` | Network outage | Re-run; cache is incremental, partial fetches are not persisted. |
| `total_savings_opportunity_usd == 0` | All shared vendors have identical avg prices across agencies | Increase `max_records`; thin cohort means the "best peer" tie wins on every cell. |

</failure-modes>

<output-contract>

When you finish, return to the user:

1. The path to the rendered HTML memo (absolute).
2. The path to the JSON sidecar (absolute).
3. Headline: `<n_contracts> contracts × <n_agencies> agencies × <n_vendors> vendors`.
4. Total savings opportunity, scaled.
5. The single biggest agency-level opportunity in one line:
   `<agency> overpaid <delta>/award on <n> awards → <savings$> recoverable`.

Do not paste the full opportunity table or vendor-spread table into chat.
The HTML is the artifact.

</output-contract>

<example>

User: "Benchmark IT support services across federal agencies for FY2024."

Agent:
  1. Calls `benchmark_vendors(psc_code="D310", fiscal_year=2024, max_records=2000)`.
  2. Reads back `json_path` to spot-check the cohort thickness.
  3. Replies:
       "Benchmarked: 2,000 contracts × 47 agencies × 312 vendors for IT
        Support Services FY2024. Total cross-buyer savings opportunity:
        $48.3M. Largest single opportunity: Department of Veterans Affairs
        paid an avg of $2.1M/award versus a best-peer avg of $640K — moving
        their 87 cohort awards to the peer benchmark would recover $12.7M.
        Widest-spread vendor in the cohort: Acme IT Inc. ($85K min /
        $1.4M max — 16.5× spread across 6 agency customers).
        Memo: finance_output/benchmark_D310_FY2024.html"

</example>
