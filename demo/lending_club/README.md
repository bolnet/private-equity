# Lending Club demo — real consumer-lending data

A 30k-loan slice of real Lending Club originations (Jan 2015 – Dec 2016, mature outcomes only). Used to demonstrate the Decision-Optimization Diagnostic on actual lender cashflow data.

## Files

| File | Rows | What it is |
|---|---|---|
| `loans.csv` | 30,000 | Underwriting record — grade, term, purpose, state, funded_amnt, rate, borrower attributes |
| `performance.csv` | 30,000 | Servicing outcome — loan_status, total_pymnt, recoveries |

Outcome column (computed by the `lending_b2c` template at ingest time):

```
outcome_usd = total_pymnt + recoveries − funded_amnt
```

Positive = profitable loan. Negative = lost money.

## Source

Pulled from the public HuggingFace dataset [codesignal/lending-club-loan-accepted](https://huggingface.co/datasets/codesignal/lending-club-loan-accepted) (original source: Lending Club's own public loan history, archived after the platform's retail pivot).

## Regenerate

```bash
python -m demo.lending_club.slice \
    --out demo/lending_club \
    --n 30000 --start 2015-01 --end 2016-12 --seed 42
```

First run downloads the ~1.6 GB source CSV via HuggingFace Hub. Subsequent runs reuse the HF cache. Set `LENDING_CLUB_CSV=/path/to/local.csv` to skip the download entirely.

## What the diagnostic finds

Running `/diagnose-decisions` on this slice surfaces the classic sub-prime refi pattern:

- Grade F/G × debt_consolidation — losing ~$465k/yr combined, negative in 7/8 quarters
- Grade E × credit_card and debt_consolidation — losing ~$225k/yr, structurally unprofitable
- Total identified opportunity ≈ $800k/yr against a ~$14.5M book contribution

Real finding. Real data. Real PE conversation starter.
