"""
USAspending.gov fetcher — public no-auth federal-contract awards data.

Endpoint: POST https://api.usaspending.gov/api/v2/search/spending_by_award/

USAspending requires a User-Agent header identifying the requester. No API
key is needed. The whole stack is stdlib (urllib.request) so this module
ships without an HTTP dependency.

We page through `spending_by_award` using `{filters, fields, page, limit,
sort, order, subawards}`. For the procurement-benchmarking demo each
result row contributes:

  - Awarding Agency       → treated as a "portco" buyer
  - Recipient Name        → vendor
  - Award Amount          → $ paid
  - PSC                   → product/service code (the SKU bucket)
  - NAICS                 → industry classification
  - Description           → free-text; useful for spot-checks
  - Start Date / End Date → period of performance

Cache: every (psc_code, fiscal_year, max_records) tuple writes a JSON file
to disk so re-runs do not re-hit the API. The cache is content-addressed
on the request body, so changing any filter invalidates cleanly.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

USER_AGENT = "private-equity-mcp-demo/0.1 (procurement-benchmark) contact@bolnet.io"
ENDPOINT = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
TIMEOUT_S = 60

# Award type codes for prime contracts (A/B/C/D = definitive contracts,
# purchase orders, BPA call orders, IDV awards). Excludes grants/loans.
CONTRACT_AWARD_TYPES: tuple[str, ...] = ("A", "B", "C", "D")

# Fields requested from the API. Keep stable — keys on result dicts mirror
# this list verbatim (USAspending echoes the field labels back as keys).
DEFAULT_FIELDS: tuple[str, ...] = (
    "Award ID",
    "Recipient Name",
    "Awarding Agency",
    "Awarding Sub Agency",
    "Award Amount",
    "Description",
    "Start Date",
    "End Date",
    "NAICS",
    "PSC",
    "recipient_id",
)

# Per the USAspending docs the spending_by_award endpoint allows up to 100
# results per page; we page through until either no more results or
# max_records is hit.
PAGE_LIMIT = 100


@dataclass(frozen=True)
class FetchResult:
    """One USAspending fetch outcome."""

    psc_code: str
    fiscal_year: int
    n_records: int
    cache_path: Path
    from_cache: bool


def _fiscal_year_window(fy: int) -> tuple[str, str]:
    """US federal fiscal year FY{n} = Oct 1 of (n-1) → Sep 30 of n."""
    return (f"{fy - 1}-10-01", f"{fy}-09-30")


def _build_request_body(
    psc_code: str,
    fiscal_year: int,
    page: int,
    limit: int,
) -> dict:
    """Construct the JSON body for one page of `spending_by_award`."""
    start, end = _fiscal_year_window(fiscal_year)
    return {
        "filters": {
            "award_type_codes": list(CONTRACT_AWARD_TYPES),
            "psc_codes": [psc_code],
            "time_period": [{"start_date": start, "end_date": end}],
        },
        "fields": list(DEFAULT_FIELDS),
        "page": page,
        "limit": limit,
        "sort": "Award Amount",
        "order": "desc",
        "subawards": False,
    }


def _cache_key(psc_code: str, fiscal_year: int, max_records: int) -> str:
    """Stable hash of the fetch parameters for filename addressing."""
    payload = json.dumps(
        {"psc_code": psc_code, "fiscal_year": fiscal_year, "max_records": max_records},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _http_post_json(url: str, body: dict) -> dict:
    """POST a JSON body, return decoded JSON. Raises urllib HTTPError on 4xx/5xx."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310
        return json.loads(resp.read())


def fetch_awards(
    psc_code: str,
    fiscal_year: int,
    max_records: int,
    cache_dir: str | Path,
) -> tuple[list[dict], FetchResult]:
    """Fetch federal contract awards for one PSC code + fiscal year.

    Returns:
        (records, fetch_result) where records is a list of dicts with the
        API field labels as keys, and fetch_result captures the cache state.

    The function is cache-first: if a cache file exists for the same
    (psc_code, fiscal_year, max_records) tuple, the cached records are
    returned and no network call is made.
    """
    cache_root = Path(cache_dir)
    cache_root.mkdir(parents=True, exist_ok=True)
    key = _cache_key(psc_code, fiscal_year, max_records)
    cache_path = cache_root / f"usaspending_{psc_code}_{fiscal_year}_{key}.json"

    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            records = cached.get("records") or []
            return records, FetchResult(
                psc_code=psc_code,
                fiscal_year=fiscal_year,
                n_records=len(records),
                cache_path=cache_path,
                from_cache=True,
            )
        except (json.JSONDecodeError, OSError):
            # Corrupt cache — fall through and re-fetch
            pass

    records: list[dict] = []
    page = 1
    while len(records) < max_records:
        remaining = max_records - len(records)
        page_size = min(PAGE_LIMIT, remaining)
        body = _build_request_body(psc_code, fiscal_year, page, page_size)
        try:
            payload = _http_post_json(ENDPOINT, body)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"USAspending API returned HTTP {exc.code} for PSC {psc_code} "
                f"FY{fiscal_year} page {page}: {exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"USAspending API unreachable for PSC {psc_code} FY{fiscal_year}: "
                f"{exc.reason}"
            ) from exc

        page_records = payload.get("results") or []
        if not page_records:
            break
        records.extend(page_records)

        meta = payload.get("page_metadata") or {}
        if not meta.get("hasNext"):
            break
        page += 1

    cache_path.write_text(
        json.dumps(
            {
                "psc_code": psc_code,
                "fiscal_year": fiscal_year,
                "max_records": max_records,
                "n_records": len(records),
                "records": records,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return records, FetchResult(
        psc_code=psc_code,
        fiscal_year=fiscal_year,
        n_records=len(records),
        cache_path=cache_path,
        from_cache=False,
    )
