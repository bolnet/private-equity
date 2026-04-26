"""
SEC EDGAR fetcher — public no-auth access to filings.

SEC requires a User-Agent identifying the requester. No API key needed.
We hit two endpoints:

  - https://www.sec.gov/files/company_tickers.json    (ticker → CIK map)
  - https://data.sec.gov/submissions/CIK<10-digit>.json  (filings history)
  - https://www.sec.gov/Archives/edgar/data/<cik>/<accession-no-dashes>/<file>

The whole stack is stdlib — urllib.request — so this module ships without
adding an HTTP dependency. SEC's fair-access policy: 10 req/sec; we make
fewer than that for any single 10-K fetch.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

USER_AGENT = "private-equity-mcp-demo/0.1 contact@bolnet.io"
TIMEOUT_S = 60


@dataclass(frozen=True)
class Filing:
    """One SEC filing reference."""
    cik: int
    ticker: str
    company_name: str
    form: str
    accession_no: str
    filing_date: str
    primary_document: str
    url: str


def _http_get_json(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read())


def _http_get_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return resp.read().decode("utf-8", errors="replace")


def resolve_cik(ticker: str) -> dict:
    """ticker → {cik_str, ticker, title}"""
    tickers = _http_get_json("https://www.sec.gov/files/company_tickers.json")
    if isinstance(tickers, list):
        # Some endpoints return list-of-dicts
        for v in tickers:
            if v.get("ticker", "").upper() == ticker.upper():
                return v
    elif isinstance(tickers, dict):
        for v in tickers.values():
            if isinstance(v, dict) and v.get("ticker", "").upper() == ticker.upper():
                return v
    raise ValueError(f"Ticker not found in SEC EDGAR registry: {ticker}")


def latest_form(ticker: str, form: str = "10-K") -> Filing:
    """Find the most recent filing of `form` (e.g. '10-K', '10-Q', 'S-1', '8-K')."""
    info = resolve_cik(ticker)
    cik = int(info["cik_str"])
    cik_padded = f"{cik:010d}"
    sub = _http_get_json(f"https://data.sec.gov/submissions/CIK{cik_padded}.json")

    recent = sub["filings"]["recent"]
    forms = recent["form"]
    accs = recent["accessionNumber"]
    docs = recent["primaryDocument"]
    dates = recent["filingDate"]

    idx = next((i for i, f in enumerate(forms) if f == form), None)
    if idx is None:
        raise ValueError(f"No {form} filing found for {ticker} in recent submissions.")

    accession = accs[idx]
    accession_clean = accession.replace("-", "")
    primary_doc = docs[idx]
    url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/"
        f"{accession_clean}/{primary_doc}"
    )

    return Filing(
        cik=cik,
        ticker=info["ticker"],
        company_name=sub.get("name", info.get("title", "")),
        form=form,
        accession_no=accession,
        filing_date=dates[idx],
        primary_document=primary_doc,
        url=url,
    )


def download(filing: Filing, dest_dir: Path | str = "/tmp/sec_filings") -> Path:
    """Fetch the filing document and write it to disk; return the local path."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    local = dest_dir / f"{filing.ticker}_{filing.form}_{filing.filing_date}.htm"
    if local.exists():
        return local
    text = _http_get_text(filing.url)
    local.write_text(text, encoding="utf-8")
    return local
