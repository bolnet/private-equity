# Regional Lenders demo — real public lending corpus for BX

A 5-portco "fund of regional consumer lenders" built from the public Lending Club
2015–2016 loan history, partitioned by US census region. Used to demonstrate the
**BX cross-portco benchmark** on real lender cashflow data — same source as the
single-portco DX [Lending Club demo](../lending_club/README.md), sliced into
five disjoint regional books.

## The five "portcos"

| Slug                 | States covered                                       | Loans  |
|----------------------|------------------------------------------------------|-------:|
| `northeast_lender`   | CT ME MA NH NJ NY PA RI VT                           | 12,000 |
| `midwest_lender`     | IL IN IA KS MI MN MO NE ND OH SD WI                  | 12,000 |
| `southeast_lender`   | AL AR DE DC FL GA KY LA MD MS NC OK SC TN TX VA WV   | 12,000 |
| `pacific_lender`     | AK CA HI OR WA                                       | 12,000 |
| `mountain_lender`    | AZ CO ID MT NV NM UT WY                              | 12,000 |

Each folder contains two CSVs that match the `lending_b2c` DX template:

```
demo/regional_lenders/<slug>/loans.csv          # underwriting record
demo/regional_lenders/<slug>/performance.csv    # servicing outcome
```

Outcome (computed inside DX at ingest time):

```
outcome_usd = total_pymnt + recoveries − funded_amnt
```

Positive = profitable loan. Negative = lost money.

## Source

All five regions are sampled from the same public dataset used by
`demo/lending_club/`: [codesignal/lending-club-loan-accepted](https://huggingface.co/datasets/codesignal/lending-club-loan-accepted)
on HuggingFace Hub (Lending Club's own public loan history, archived after the
platform's retail pivot).

Real data, real outcomes, partitioned to give the BX module a defensible
"5-lender fund" narrative without any synthetic generation.

## Regenerate

```bash
python -m demo.regional_lenders.slice \
    --out demo/regional_lenders \
    --per-region 12000 --start 2015-01 --end 2016-12 --seed 42
```

First run downloads the ~1.6 GB source CSV via HuggingFace Hub. Subsequent runs
reuse the HF cache. Set `LENDING_CLUB_CSV=/path/to/local.csv` to skip the
download entirely.

## Build the BX corpus

```bash
python -m scripts.build_bx_corpus
```

This runs DX on each portco (multi-archetype: pricing × selection × allocation)
to produce five OpportunityMap JSON sidecars under `finance_output/`, then
ingests them into a BX corpus and renders an LP-facing benchmark report.

Outputs:

```
finance_output/dx_report_<portco>.html   # one per region
finance_output/dx_report_<portco>.json   # OpportunityMap sidecar
finance_output/bx_report_regional_lenders_demo.html
finance_output/bx_report_regional_lenders_demo.json
```

`--skip-dx` reuses existing OpportunityMap JSONs and re-runs only BX.

## What the benchmark finds

Across the five lenders, ~$3.2M/yr of identifiable annual impact, distributed
across three decision archetypes that show up in **all 5 portcos**:

| Archetype  | Decision dimensions      | Fund-wide $ impact | Share |
|------------|--------------------------|-------------------:|------:|
| `pricing`  | grade × term             |             ~$1.4M |  45%  |
| `selection`| purpose × grade          |             ~$1.1M |  34%  |
| `allocation`| state × grade           |             ~$0.7M |  21%  |

Headline finding: **Grade E–F × 60-month is structurally loss-making in every
single regional book** — a fund-level theme, not a one-portco anomaly. Same for
debt-consolidation refis at sub-prime grades. This is exactly the kind of
cross-portco pattern the BX benchmark was designed to surface to an LP.
