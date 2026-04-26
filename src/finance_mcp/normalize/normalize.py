"""
normalize_portco — fold N portcos with heterogeneous chart-of-accounts
into a single canonical schema with per-cell provenance and cohort-level
anomaly detection.

Architecture mirrors `explainer/explain.py`:
  * Pure pandas + regex on inputs — deterministic, no ML.
  * Frozen canonical schema in `canonical_schema.py`.
  * Editorial-letterpress HTML render at the end (Cormorant Garamond +
    EB Garamond + paper cream — same typography vocabulary as the
    board memo so artifacts feel of-a-piece).

Inputs: a list of CSV paths *or* a list of directories. If a directory is
passed, the module looks for the loans + performance pair following
`dx.templates.LENDING_B2C` filename patterns.

Outputs (under finance_output/, basename driven by `output_filename`):
  * normalized.csv           — unified frame, canonical columns, +portco_id
  * mapping_audit.json       — per-portco source→canonical column mapping
                                + per-cell provenance (source file, col)
  * anomalies.json           — magnitude / sign / coverage flags
  * report.html              — editorial-letterpress side-by-side digest
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import pandas as pd
from fastmcp.exceptions import ToolError

from finance_mcp.normalize.canonical_schema import (
    CANONICAL_FIELDS,
    CanonicalField,
    canonical_names,
    currency_fields,
    field_by_name,
    required_names,
)

# ---------------------------------------------------------------------------
# Constants — no hardcoded magic numbers buried in the logic
# ---------------------------------------------------------------------------

_LOAN_FILENAME_HINTS: tuple[str, ...] = ("loan",)
_PERF_FILENAME_HINTS: tuple[str, ...] = ("perf", "servicing", "repay", "payment")

_MAGNITUDE_OUTLIER_RATIO: float = 10.0       # >10× or <1/10 the corpus median
_COVERAGE_FLAG_THRESHOLD: float = 0.5         # column missing in >50% of portco
_SIGN_FLIP_MIN_OBS: int = 30                  # min rows before we trust sign

_FUZZY_TOKEN_HIT_FLOOR: float = 0.5           # min Jaccard for fuzzy fallback
                                                #   At 0.5, 'sub_grade' fuzzy-
                                                #   maps to 'grade' (correct on
                                                #   most lender taxonomies).
                                                #   The collision-resolution
                                                #   step in _ingest_portco
                                                #   prefers exact matches over
                                                #   fuzzy when the same target
                                                #   has multiple claimants.


# ---------------------------------------------------------------------------
# Frozen DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnMapping:
    """One source column's resolution to (or rejection from) the canonical schema."""

    source_column: str
    canonical_field: str | None    # None = unmapped
    match_method: str              # 'alias' | 'regex' | 'fuzzy_token' | 'unmapped'
    match_score: float             # 1.0 for alias/regex hits, Jaccard for fuzzy
    source_file: str               # absolute path of the CSV the column came from


@dataclass(frozen=True)
class PortcoIngestion:
    """All the bookkeeping for one portco's normalization pass."""

    portco_id: str
    source_paths: tuple[str, ...]
    column_mappings: tuple[ColumnMapping, ...]
    n_rows: int
    missing_required: tuple[str, ...]


@dataclass(frozen=True)
class Anomaly:
    """One cohort-level anomaly the normalizer flagged."""

    kind: str                  # 'magnitude' | 'sign_flip' | 'coverage'
    canonical_field: str
    portco_id: str
    severity: str              # 'high' | 'medium' | 'low'
    detail: str                # human-readable narrative
    metric_value: float | None
    corpus_baseline: float | None


# ---------------------------------------------------------------------------
# Step 1 — locate CSV pairs from each portco input path
# ---------------------------------------------------------------------------


def _resolve_portco_files(path_str: str) -> tuple[Path, ...]:
    """Resolve a portco input string to its CSV file(s).

    Accepts:
      * A directory containing loans.csv + performance.csv (preferred shape)
      * A single CSV file
      * A directory with multiple CSVs — all picked up
    """
    p = Path(path_str)
    if not p.exists():
        raise ToolError(f"Portco path does not exist: {p}")

    if p.is_file():
        if p.suffix.lower() != ".csv":
            raise ToolError(f"Portco file must be a CSV (got {p.suffix}): {p}")
        return (p,)

    if p.is_dir():
        csvs = tuple(sorted(p.glob("*.csv")))
        if not csvs:
            raise ToolError(f"No CSV files found in directory: {p}")
        return csvs

    raise ToolError(f"Portco path is neither file nor directory: {p}")


def _classify_csv(filename: str) -> str:
    """Best-effort classification of a CSV by filename."""
    lower = filename.lower()
    if any(hint in lower for hint in _PERF_FILENAME_HINTS):
        return "performance"
    if any(hint in lower for hint in _LOAN_FILENAME_HINTS):
        return "loans"
    return "unknown"


# ---------------------------------------------------------------------------
# Step 2 — fuzzy column-name matching (no ML, pure string distance)
# ---------------------------------------------------------------------------


def _normalize_colname(s: str) -> str:
    """Lowercase, strip non-alphanumeric, collapse whitespace.

    'Loan Amount' → 'loan_amount', 'LoanID' → 'loanid', 'Total Pymnt $' → 'total_pymnt'.
    """
    cleaned = re.sub(r"[^a-z0-9]+", "_", s.strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned


def _tokenize(s: str) -> set[str]:
    """Tokenize a normalized column name on underscores.

    'loan_amount_usd' → {'loan', 'amount', 'usd'}
    """
    return {tok for tok in s.split("_") if tok}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity of two token sets — 0.0..1.0, deterministic."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _match_column(
    source_col: str,
    fields: tuple[CanonicalField, ...] = CANONICAL_FIELDS,
) -> tuple[str | None, str, float]:
    """Resolve one source column to the best canonical field.

    Match-method precedence:
      1. exact alias hit              (score 1.0, method 'alias')
      2. regex pattern hit            (score 1.0, method 'regex')
      3. token-Jaccard above floor    (score = jaccard, method 'fuzzy_token')
      4. unmapped                     (score 0.0, method 'unmapped')
    """
    norm = _normalize_colname(source_col)

    # 1. exact alias hit (after normalization)
    for f in fields:
        norm_aliases = {_normalize_colname(a) for a in f.aliases}
        if norm in norm_aliases:
            return f.name, "alias", 1.0

    # 2. regex hit (apply against normalized form for stability)
    for f in fields:
        for pat in f.regex_patterns:
            if re.search(pat, norm):
                return f.name, "regex", 1.0

    # 3. fuzzy token Jaccard fallback
    src_tokens = _tokenize(norm)
    best_field: str | None = None
    best_score = 0.0
    for f in fields:
        # Build token vocab from the canonical name + all aliases
        candidate_token_sets = [_tokenize(_normalize_colname(f.name))]
        candidate_token_sets += [_tokenize(_normalize_colname(a)) for a in f.aliases]
        for cand in candidate_token_sets:
            score = _jaccard(src_tokens, cand)
            if score > best_score:
                best_score = score
                best_field = f.name

    if best_field is not None and best_score >= _FUZZY_TOKEN_HIT_FLOOR:
        return best_field, "fuzzy_token", best_score

    return None, "unmapped", 0.0


# ---------------------------------------------------------------------------
# Step 3 — coerce dtypes per the canonical schema
# ---------------------------------------------------------------------------


def _coerce_series(series: pd.Series, field: CanonicalField) -> pd.Series:
    """Coerce a Series to the canonical dtype family. Errors → NaN, no raise."""
    if field.dtype == "numeric":
        return pd.to_numeric(series, errors="coerce")
    if field.dtype == "date":
        # Try multiple formats. pandas' default infers most.
        return pd.to_datetime(series, errors="coerce", format="mixed")
    # 'string' family
    return series.astype("string")


# ---------------------------------------------------------------------------
# Step 4 — per-portco normalization
# ---------------------------------------------------------------------------


def _ingest_portco(portco_id: str, csv_paths: tuple[Path, ...]) -> tuple[pd.DataFrame, PortcoIngestion]:
    """Load + map + merge one portco's CSVs into a canonical frame.

    Returns:
      * normalized DataFrame (canonical columns + portco_id)
      * PortcoIngestion bookkeeping
    """
    frames_by_role: dict[str, pd.DataFrame] = {}
    all_mappings: list[ColumnMapping] = []

    for csv_path in csv_paths:
        try:
            raw = pd.read_csv(csv_path)
        except Exception as exc:
            raise ToolError(f"[{portco_id}] failed to read {csv_path}: {exc}") from exc

        role = _classify_csv(csv_path.name)

        # Resolve every column on this CSV. Audit every column (mapped or not)
        # for the provenance record. Resolve target collisions (two source
        # columns claiming the same canonical field) by picking the highest-
        # priority match: alias > regex > fuzzy_token, with score as tiebreaker.
        method_priority = {"alias": 3, "regex": 2, "fuzzy_token": 1, "unmapped": 0}
        per_target_winner: dict[str, tuple[str, int, float]] = {}  # tgt -> (src, prio, score)

        for src_col in raw.columns:
            target, method, score = _match_column(src_col)
            all_mappings.append(
                ColumnMapping(
                    source_column=src_col,
                    canonical_field=target,
                    match_method=method,
                    match_score=score,
                    source_file=str(csv_path),
                )
            )
            if target is None:
                continue
            prio = method_priority[method]
            current = per_target_winner.get(target)
            if current is None or (prio, score) > (current[1], current[2]):
                per_target_winner[target] = (src_col, prio, score)

        if not per_target_winner:
            continue

        # Build the rename map: source col → canonical target
        rename = {src: tgt for tgt, (src, _, _) in per_target_winner.items()}
        sliced = raw[list(rename.keys())].rename(columns=rename)

        # Coerce dtypes per canonical schema
        for col in sliced.columns:
            f = field_by_name(col)
            if f is not None:
                sliced[col] = _coerce_series(sliced[col], f)

        # Merge by role: stack 'loans' as the spine, 'performance' joined on loan_id
        if role in frames_by_role:
            # If two CSVs share the same role, vertical-stack
            frames_by_role[role] = pd.concat(
                [frames_by_role[role], sliced], ignore_index=True
            )
        else:
            frames_by_role[role] = sliced

    if not frames_by_role:
        raise ToolError(f"[{portco_id}] no canonical columns mappable from any input CSV.")

    # Spine selection: prefer 'loans', else any
    if "loans" in frames_by_role:
        spine = frames_by_role["loans"]
    else:
        first_role = next(iter(frames_by_role))
        spine = frames_by_role[first_role]

    # Left-join performance onto the spine if both exist
    if "loans" in frames_by_role and "performance" in frames_by_role:
        perf = frames_by_role["performance"]
        if "loan_id" in spine.columns and "loan_id" in perf.columns:
            # Avoid column collision — drop overlapping cols on perf side except join key
            overlap = (set(spine.columns) & set(perf.columns)) - {"loan_id"}
            perf = perf.drop(columns=list(overlap), errors="ignore")
            spine = spine.merge(perf, on="loan_id", how="left")
        else:
            # fall back to side-by-side if join key is absent on either side
            spine = pd.concat([spine, perf], axis=1)

    # If 'unknown' role frames exist, side-by-side concat them too
    for role, frame in frames_by_role.items():
        if role in {"loans", "performance"}:
            continue
        # only add columns we don't already have
        new_cols = [c for c in frame.columns if c not in spine.columns]
        if new_cols:
            spine = pd.concat([spine, frame[new_cols]], axis=1)

    # Add canonical columns missing from this portco as NaN, in canonical order
    for canon in canonical_names():
        if canon not in spine.columns:
            spine[canon] = pd.NA
    spine = spine[list(canonical_names())]
    spine.insert(0, "portco_id", portco_id)

    missing_required = tuple(
        name for name in required_names()
        if spine[name].isna().all()
    )

    ingestion = PortcoIngestion(
        portco_id=portco_id,
        source_paths=tuple(str(p) for p in csv_paths),
        column_mappings=tuple(all_mappings),
        n_rows=int(len(spine)),
        missing_required=missing_required,
    )
    return spine, ingestion


# ---------------------------------------------------------------------------
# Step 5 — cohort-level anomaly detection
# ---------------------------------------------------------------------------


def _detect_anomalies(
    unified: pd.DataFrame, ingestions: tuple[PortcoIngestion, ...]
) -> tuple[Anomaly, ...]:
    """Three checks: magnitude outliers, sign flips, coverage gaps."""
    anomalies: list[Anomaly] = []
    portco_ids = tuple(unified["portco_id"].dropna().unique().tolist())

    # 1. Magnitude — currency fields. Detection logic:
    #    Flag any portco whose median is >= MAGNITUDE_OUTLIER_RATIO times the
    #    smallest peer median (or <= 1/RATIO of the largest peer median). We
    #    compare to peer extremes — not the corpus median — because a single
    #    outlier biases the median, and the operating-partner question is
    #    "are these dollars even on the same scale?", which is a min/max
    #    spread question, not a center question.
    for col in currency_fields():
        if col not in unified.columns:
            continue
        per_portco_median: dict[str, float] = {}
        for pid in portco_ids:
            slice_ = unified.loc[unified["portco_id"] == pid, col].dropna()
            if len(slice_) >= _SIGN_FLIP_MIN_OBS:
                med = float(slice_.abs().median())
                if med > 0:
                    per_portco_median[pid] = med

        if len(per_portco_median) < 2:
            continue

        min_med = min(per_portco_median.values())
        max_med = max(per_portco_median.values())
        if min_med <= 0:
            continue

        spread = max_med / min_med
        if spread < _MAGNITUDE_OUTLIER_RATIO:
            continue  # everything roughly on-scale

        # Flag the extremes — both the smallest and the largest portco
        for pid, med in per_portco_median.items():
            is_low = med == min_med
            is_high = med == max_med
            if not (is_low or is_high):
                continue
            peer = max_med if is_low else min_med
            ratio = peer / med if is_low else med / peer
            severity = "high" if ratio >= 30 else "medium"
            direction = "below" if is_low else "above"
            anomalies.append(
                Anomaly(
                    kind="magnitude",
                    canonical_field=col,
                    portco_id=pid,
                    severity=severity,
                    detail=(
                        f"{pid}'s median |{col}| = {med:,.2f} is {ratio:.1f}× "
                        f"{direction} the peer extreme ({peer:,.2f}). "
                        f"Cohort spread = {spread:.1f}×. Likely unit "
                        f"mismatch (cents vs dollars, thousands vs ones) "
                        f"or genuinely different product mix."
                    ),
                    metric_value=med,
                    corpus_baseline=peer,
                )
            )

    # 2. Sign flip — currency fields where some portcos skew positive, others negative
    for col in currency_fields():
        if col not in unified.columns:
            continue
        per_portco_sign: dict[str, float] = {}
        for pid in portco_ids:
            slice_ = unified.loc[unified["portco_id"] == pid, col].dropna()
            if len(slice_) >= _SIGN_FLIP_MIN_OBS:
                pos_share = float((slice_ > 0).mean())
                per_portco_sign[pid] = pos_share

        if len(per_portco_sign) < 2:
            continue
        signs = list(per_portco_sign.values())
        spread = max(signs) - min(signs)
        if spread > 0.5:  # one portco mostly positive, another mostly negative
            for pid, share in per_portco_sign.items():
                if share < 0.5 and any(s > 0.8 for s in signs):
                    anomalies.append(
                        Anomaly(
                            kind="sign_flip",
                            canonical_field=col,
                            portco_id=pid,
                            severity="medium",
                            detail=(
                                f"{pid}'s {col} is positive in only {share:.0%} of "
                                f"rows while peers are >80% positive. Likely sign "
                                f"convention flipped (debit vs credit ledger)."
                            ),
                            metric_value=share,
                            corpus_baseline=None,
                        )
                    )

    # 3. Coverage — required canonical fields empty in some portcos
    for col in canonical_names():
        f = field_by_name(col)
        if f is None:
            continue
        for pid in portco_ids:
            slice_ = unified.loc[unified["portco_id"] == pid, col]
            if len(slice_) == 0:
                continue
            missing_share = float(slice_.isna().mean())
            if missing_share > _COVERAGE_FLAG_THRESHOLD:
                severity = "high" if (f.required and missing_share > 0.9) else "low"
                anomalies.append(
                    Anomaly(
                        kind="coverage",
                        canonical_field=col,
                        portco_id=pid,
                        severity=severity,
                        detail=(
                            f"{pid} has {missing_share:.0%} missing in "
                            f"canonical field '{col}'"
                            + (" (REQUIRED)" if f.required else "")
                            + ". No source column mapped, or all mapped values were null."
                        ),
                        metric_value=missing_share,
                        corpus_baseline=None,
                    )
                )

    return tuple(anomalies)


# ---------------------------------------------------------------------------
# Step 6 — HTML render (editorial-letterpress, twin to explain.py)
# ---------------------------------------------------------------------------


_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Normalized portfolio digest</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&family=EB+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&family=Newsreader:ital,wght@0,300;1,300&family=JetBrains+Mono:wght@400&display=swap" rel="stylesheet" />
<style>
  :root {{
    --paper:    #f4ecd5;
    --page:     #fbf6e2;
    --ink:      #1a140d;
    --ink-dim:  #5a4a35;
    --ink-faint:#8b765a;
    --rule:     #c2ad84;
    --rule-soft:#dfd2af;
    --accent:   #6b1414;
    --accent-2: #93331f;
    --gold:     #8a6f1a;
    --max:      980px;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  html {{ background: #ece4cb; }}
  body {{
    background:
      radial-gradient(ellipse 1200px 800px at 50% -100px, rgba(255,248,220,0.6), transparent 70%),
      radial-gradient(ellipse 600px 400px at 80% 120%, rgba(107,20,20,0.04), transparent 70%),
      var(--paper);
    color: var(--ink);
    font-family: 'EB Garamond', 'Iowan Old Style', Georgia, serif;
    font-size: 17px;
    line-height: 1.6;
    font-feature-settings: "liga", "dlig", "onum", "kern";
    text-rendering: optimizeLegibility;
    -webkit-font-smoothing: antialiased;
    padding: 64px 20px 96px;
  }}
  body::before {{
    content: ''; position: fixed; inset: 0;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0.42  0 0 0 0 0.34  0 0 0 0 0.18  0 0 0 0.07 0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>");
    opacity: 0.32; pointer-events: none; z-index: 0; mix-blend-mode: multiply;
  }}
  .sheet {{
    max-width: var(--max); margin: 0 auto; background: var(--page);
    position: relative; z-index: 1; padding: 80px 76px 72px;
    box-shadow:
      0 1px 0 var(--rule-soft),
      0 30px 60px -30px rgba(60, 40, 15, 0.18),
      0 8px 18px -6px rgba(60, 40, 15, 0.08);
    border: 1px solid rgba(194, 173, 132, 0.45);
  }}
  .letterhead {{
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: 24px; margin-bottom: 48px;
  }}
  .wordmark {{
    font-family: 'Cormorant Garamond', serif; font-weight: 500; font-style: italic;
    font-size: 21px; letter-spacing: 0.01em;
    display: flex; align-items: center; gap: 14px; color: var(--ink);
  }}
  .wordmark .seal {{
    width: 36px; height: 36px; border-radius: 50%;
    border: 1px solid var(--accent); color: var(--accent);
    display: inline-flex; align-items: center; justify-content: center;
    font-family: 'Cormorant Garamond', serif; font-style: italic;
    font-size: 18px; font-weight: 500;
    background: rgba(107, 20, 20, 0.04);
  }}
  .letterhead-meta {{
    text-align: right; font-family: 'Newsreader', 'EB Garamond', serif;
    font-style: italic; font-weight: 300; font-size: 13px; line-height: 1.55;
    color: var(--ink-faint); letter-spacing: 0.02em;
  }}
  .letterhead-meta strong {{
    display: block; color: var(--ink-dim); font-style: normal; font-weight: 500;
    font-variant: small-caps; letter-spacing: 0.14em; font-size: 11px;
    margin-bottom: 2px;
  }}

  .eyebrow {{
    font-variant: small-caps; letter-spacing: 0.18em; font-size: 12px;
    color: var(--accent); font-weight: 600; margin-bottom: 14px;
    display: inline-block;
  }}
  .eyebrow::before {{ content: '— '; color: var(--rule); }}
  .eyebrow::after  {{ content: ' —'; color: var(--rule); }}
  h1 {{
    font-family: 'Cormorant Garamond', serif; font-weight: 400;
    font-size: 46px; line-height: 1.06; letter-spacing: -0.005em;
    margin: 0 0 18px; color: var(--ink);
  }}
  h1 em {{ font-style: italic; color: var(--accent); font-weight: 500; }}
  .lede {{
    font-family: 'EB Garamond', serif; font-style: italic; color: var(--ink-dim);
    font-size: 19px; line-height: 1.55; margin: 0; max-width: 56ch;
  }}

  .ornament {{
    text-align: center; color: var(--rule);
    font-family: 'Cormorant Garamond', serif;
    font-size: 22px; letter-spacing: 1.2em;
    margin: 40px 0; padding-left: 1.2em;
  }}
  .ornament::before {{ content: '✦  ✦  ✦'; }}

  .stats-strip {{
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 0; margin: 28px 0 48px;
    border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule);
    padding: 18px 0;
  }}
  .stat {{ text-align: center; padding: 0 16px; border-right: 1px solid var(--rule-soft); }}
  .stat:last-child {{ border-right: none; }}
  .stat-label {{
    font-variant: small-caps; letter-spacing: 0.16em; font-size: 11px;
    color: var(--ink-faint); font-weight: 500; margin-bottom: 6px;
  }}
  .stat-num {{
    font-family: 'Cormorant Garamond', serif; font-weight: 500;
    font-size: 26px; line-height: 1; color: var(--ink);
    font-feature-settings: "lnum";
  }}
  .stat-sub {{
    font-family: 'Newsreader', serif; font-style: italic; font-weight: 300;
    font-size: 11px; color: var(--ink-faint); margin-top: 4px;
  }}

  h2 {{
    font-family: 'Cormorant Garamond', serif; font-weight: 500;
    font-size: 30px; margin: 48px 0 12px; color: var(--ink);
  }}
  h2 .pretitle {{
    display: block;
    font-variant: small-caps; letter-spacing: 0.18em;
    font-size: 11px; color: var(--accent); font-weight: 600;
    margin-bottom: 6px;
  }}

  table.ledger {{
    width: 100%; border-collapse: collapse;
    font-family: 'EB Garamond', serif; font-size: 14px;
    margin: 12px 0 32px; font-feature-settings: "lnum", "tnum";
  }}
  table.ledger th, table.ledger td {{
    text-align: left; padding: 8px 10px;
    border-bottom: 1px dotted var(--rule-soft);
  }}
  table.ledger th {{
    font-variant: small-caps; letter-spacing: 0.14em; font-size: 11px;
    font-weight: 600; color: var(--ink-faint);
    border-bottom: 1px solid var(--rule);
  }}
  table.ledger td.num {{ text-align: right; font-feature-settings: "lnum","tnum"; }}
  table.ledger td.unmap {{ color: var(--ink-faint); font-style: italic; }}
  table.ledger td code {{
    font-family: 'JetBrains Mono', monospace; font-size: 12px;
    color: var(--ink-dim); background: rgba(194, 173, 132, 0.18);
    padding: 1px 5px; border-radius: 2px;
  }}
  .pill {{
    display: inline-block; font-size: 10px; letter-spacing: 0.12em;
    text-transform: uppercase; padding: 1px 8px; border-radius: 10px;
    border: 1px solid var(--rule); color: var(--ink-dim);
    background: rgba(255, 247, 215, 0.6); margin-left: 4px;
  }}
  .pill.alias  {{ border-color: var(--gold);    color: var(--gold); }}
  .pill.regex  {{ border-color: var(--accent-2); color: var(--accent-2); }}
  .pill.fuzzy  {{ border-color: var(--accent);  color: var(--accent); }}
  .pill.unmap  {{ border-color: var(--ink-faint); color: var(--ink-faint); }}

  .anomaly {{
    border-left: 3px solid var(--accent);
    background: rgba(255, 247, 215, 0.55);
    padding: 14px 18px; margin: 12px 0;
  }}
  .anomaly.sev-high   {{ border-left-color: var(--accent); }}
  .anomaly.sev-medium {{ border-left-color: var(--accent-2); }}
  .anomaly.sev-low    {{ border-left-color: var(--gold); }}
  .anomaly .a-head {{
    font-variant: small-caps; letter-spacing: 0.14em; font-size: 11px;
    color: var(--accent); font-weight: 600; margin-bottom: 4px;
  }}
  .anomaly p {{ margin: 0; }}

  .colophon {{
    margin-top: 64px; padding-top: 22px;
    border-top: 1px solid var(--rule);
    font-family: 'Newsreader', 'EB Garamond', serif;
    font-style: italic; font-weight: 300;
    font-size: 12px; line-height: 1.6; color: var(--ink-faint);
    text-align: center;
  }}
  .colophon code {{
    font-family: 'JetBrains Mono', monospace; font-style: normal;
    font-size: 11px; color: var(--ink-dim);
    background: rgba(194, 173, 132, 0.18);
    padding: 1px 6px; border-radius: 2px;
  }}
  .colophon .signature {{
    font-family: 'Cormorant Garamond', serif; font-style: italic;
    font-size: 16px; color: var(--ink-dim); margin-bottom: 12px;
  }}
  .colophon .signature::before {{
    content: ''; display: block; width: 120px; height: 1px;
    background: var(--ink-faint); opacity: 0.5; margin: 0 auto 10px;
  }}

  @media (max-width: 720px) {{
    body {{ padding: 32px 8px 64px; font-size: 16px; }}
    .sheet {{ padding: 48px 24px 56px; }}
    h1 {{ font-size: 32px; }}
    .stats-strip {{ grid-template-columns: repeat(2, 1fr); gap: 12px; padding: 14px 0; }}
    .stat {{ border-right: none; border-bottom: 1px dotted var(--rule-soft); padding-bottom: 12px; }}
    .letterhead {{ flex-direction: column; gap: 8px; }}
    .letterhead-meta {{ text-align: left; }}
  }}
  @media print {{
    html, body {{ background: white; }}
    body::before {{ display: none; }}
    .sheet {{ box-shadow: none; border: none; padding: 24mm; max-width: none; }}
  }}
</style>
</head>
<body>
<article class="sheet">

  <header class="letterhead">
    <div class="wordmark">
      <span class="seal">&para;</span>
      <span>Private Equity &times; AI</span>
    </div>
    <div class="letterhead-meta">
      <strong>Normalization digest</strong>
      {n_portcos} portcos &middot; {n_rows_total:,} rows<br />
      {as_of}
    </div>
  </header>

  <div class="eyebrow">A unified chart-of-accounts across the portfolio</div>
  <h1>{n_portcos} portcos, <em>one comparable view.</em></h1>
  <p class="lede">{lede}</p>

  <div class="stats-strip">
    <div class="stat">
      <div class="stat-label">Portcos folded</div>
      <div class="stat-num">{n_portcos}</div>
      <div class="stat-sub">distinct schemas</div>
    </div>
    <div class="stat">
      <div class="stat-label">Rows normalized</div>
      <div class="stat-num">{n_rows_total:,}</div>
      <div class="stat-sub">canonical loans</div>
    </div>
    <div class="stat">
      <div class="stat-label">Canonical fields</div>
      <div class="stat-num">{n_fields}</div>
      <div class="stat-sub">target schema</div>
    </div>
    <div class="stat">
      <div class="stat-label">Anomalies</div>
      <div class="stat-num">{n_anomalies}</div>
      <div class="stat-sub">flagged</div>
    </div>
  </div>

  <div class="ornament"></div>

  {mapping_blocks}

  <h2><span class="pretitle">III</span>Anomaly digest</h2>
  {anomaly_blocks}

  <footer class="colophon">
    <div class="signature">Composed at the chart-of-accounts layer.</div>
    Generated by <code>normalize_portco</code>. Every cell in
    <code>{normalized_csv}</code> traces to a source column &mdash;
    see <code>mapping_audit.json</code> for provenance.<br />
    {as_of}.
  </footer>

</article>
</body>
</html>
"""


def _render_mapping_block(ingestion: PortcoIngestion, idx: int) -> str:
    """Per-portco mapping table — source column → canonical field, with method pill."""
    rows = []
    for m in ingestion.column_mappings:
        if m.canonical_field is None:
            target_html = '<td class="unmap">—</td><td><span class="pill unmap">unmapped</span></td>'
        else:
            method_class = {
                "alias": "alias",
                "regex": "regex",
                "fuzzy_token": "fuzzy",
            }.get(m.match_method, "alias")
            score_label = (
                f"{m.match_score:.2f}" if m.match_method == "fuzzy_token" else "1.00"
            )
            target_html = (
                f'<td><code>{m.canonical_field}</code></td>'
                f'<td><span class="pill {method_class}">{m.match_method}</span> '
                f'<span class="pill">{score_label}</span></td>'
            )
        src_basename = Path(m.source_file).name
        rows.append(
            f"<tr><td><code>{m.source_column}</code></td>{target_html}"
            f'<td class="num"><code>{src_basename}</code></td></tr>'
        )

    missing_note = ""
    if ingestion.missing_required:
        missing = ", ".join(f"<code>{m}</code>" for m in ingestion.missing_required)
        missing_note = (
            f'<p style="margin-top:6px;color:var(--accent);font-style:italic;">'
            f"Required canonical fields with no source mapping: {missing}."
            f"</p>"
        )

    roman = _to_roman(idx)
    return f"""
  <h2><span class="pretitle">{roman}</span>{ingestion.portco_id}</h2>
  <p style="margin:0 0 6px;color:var(--ink-dim);font-style:italic;">
    {ingestion.n_rows:,} rows &middot; {len(ingestion.source_paths)} source file(s)
  </p>
  {missing_note}
  <table class="ledger">
    <thead>
      <tr><th>Source column</th><th>Canonical field</th><th>Method</th><th>Source file</th></tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
"""


def _render_anomaly_block(a: Anomaly) -> str:
    return f"""
  <div class="anomaly sev-{a.severity}">
    <div class="a-head">{a.kind} &middot; {a.canonical_field} &middot; {a.portco_id} &middot; {a.severity}</div>
    <p>{a.detail}</p>
  </div>
"""


def _to_roman(n: int) -> str:
    """Tiny roman numeral helper for the section markers (matches explain.py vibe)."""
    table = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ]
    out, x = "", n
    for value, symbol in table:
        while x >= value:
            out += symbol
            x -= value
    return out or "I"


# ---------------------------------------------------------------------------
# Step 7 — orchestrator (the public tool)
# ---------------------------------------------------------------------------


def _validate_inputs(
    portco_csv_paths: list[str], portco_ids: list[str]
) -> None:
    """Fail-fast validation on the input boundary."""
    if not isinstance(portco_csv_paths, list) or not isinstance(portco_ids, list):
        raise ToolError("portco_csv_paths and portco_ids must both be lists.")
    if len(portco_csv_paths) < 2:
        raise ToolError(
            f"normalize_portco needs >= 2 portcos to be useful (got {len(portco_csv_paths)})."
        )
    if len(portco_csv_paths) != len(portco_ids):
        raise ToolError(
            f"portco_csv_paths ({len(portco_csv_paths)}) and portco_ids "
            f"({len(portco_ids)}) must be the same length."
        )
    if len(set(portco_ids)) != len(portco_ids):
        raise ToolError(f"portco_ids must be unique: {portco_ids}")


def _resolve_output_dir() -> Path:
    """Match the existing convention: write to `finance_output/` at repo root."""
    # Walk up from this file to find a sibling `finance_output/` dir.
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "finance_output"
        if candidate.exists() and candidate.is_dir():
            return candidate
    # Fallback: cwd
    cwd_candidate = Path.cwd() / "finance_output"
    cwd_candidate.mkdir(parents=True, exist_ok=True)
    return cwd_candidate


def normalize_portco(
    portco_csv_paths: list[str],
    portco_ids: list[str],
    output_filename: str | None = None,
) -> dict:
    """
    Fold N portcos with heterogeneous chart-of-accounts into a single
    canonical schema with per-cell provenance and cohort-level anomaly
    detection.

    Args:
        portco_csv_paths: List of paths. Each entry can be a directory
            (containing loans.csv + performance.csv) or a single CSV file.
            Must align positionally with `portco_ids`.
        portco_ids: List of portco identifiers (same length, unique).
        output_filename: Optional basename stem for the output artifacts.
            Defaults to ``normalize_<n>portcos``.

    Returns:
        dict with:
          - report_path:        absolute path to the HTML digest
          - normalized_csv_path: absolute path to the unified CSV
          - mapping_audit_path:  absolute path to the source-→canonical
                                  mapping JSON (with provenance)
          - anomalies_path:      absolute path to the cohort-anomaly JSON
          - n_portcos:           int
          - n_rows_normalized:   int

    Raises:
        ToolError: on any validation / IO failure.
    """
    _validate_inputs(portco_csv_paths, portco_ids)

    # Resolve every portco's CSV(s)
    resolved: list[tuple[str, tuple[Path, ...]]] = []
    for portco_id, raw_path in zip(portco_ids, portco_csv_paths):
        csvs = _resolve_portco_files(raw_path)
        resolved.append((portco_id, csvs))

    # Ingest each portco
    frames: list[pd.DataFrame] = []
    ingestions: list[PortcoIngestion] = []
    for portco_id, csvs in resolved:
        df, ingestion = _ingest_portco(portco_id, csvs)
        frames.append(df)
        ingestions.append(ingestion)

    unified = pd.concat(frames, ignore_index=True)

    # Detect anomalies
    anomalies = _detect_anomalies(unified, tuple(ingestions))

    # Write outputs
    out_dir = _resolve_output_dir()
    stem = output_filename or f"normalize_{len(portco_ids)}portcos"
    # Strip an .html suffix if the caller supplied one — we manage extensions
    if stem.endswith(".html"):
        stem = stem[:-5]

    normalized_csv_path = out_dir / f"{stem}.csv"
    mapping_audit_path = out_dir / f"{stem}.mapping_audit.json"
    anomalies_path = out_dir / f"{stem}.anomalies.json"
    report_path = out_dir / f"{stem}.html"

    # 1. CSV
    unified.to_csv(normalized_csv_path, index=False)

    # 2. Mapping audit JSON — frozen DTO → dict
    mapping_audit = {
        "as_of": date.today().isoformat(),
        "n_portcos": len(ingestions),
        "n_rows_total": int(len(unified)),
        "canonical_schema": [asdict(f) for f in CANONICAL_FIELDS],
        "portcos": [
            {
                "portco_id": ing.portco_id,
                "source_paths": list(ing.source_paths),
                "n_rows": ing.n_rows,
                "missing_required": list(ing.missing_required),
                "column_mappings": [asdict(m) for m in ing.column_mappings],
            }
            for ing in ingestions
        ],
    }
    mapping_audit_path.write_text(json.dumps(mapping_audit, indent=2), encoding="utf-8")

    # 3. Anomalies JSON
    anomalies_path.write_text(
        json.dumps(
            {
                "as_of": date.today().isoformat(),
                "n_anomalies": len(anomalies),
                "anomalies": [asdict(a) for a in anomalies],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # 4. HTML report
    mapping_blocks = "\n".join(
        _render_mapping_block(ing, idx + 1) for idx, ing in enumerate(ingestions)
    )
    if anomalies:
        anomaly_blocks = "\n".join(_render_anomaly_block(a) for a in anomalies)
    else:
        anomaly_blocks = (
            '<p style="color:var(--ink-faint);font-style:italic;">'
            "No anomalies surfaced. Magnitudes, signs, and coverage all align "
            "across portcos."
            "</p>"
        )

    lede = (
        f"A cross-portco normalization pass folded {len(ingestions)} distinct "
        f"chart-of-accounts into the canonical {len(CANONICAL_FIELDS)}-field "
        f"schema. Every cell in the unified CSV traces back to a source file "
        f"and source column; the digest below names the mapping for each "
        f"portco and surfaces the cohort-level anomalies the operating "
        f"partner should resolve before treating the data as comparable."
    )

    html = _HTML.format(
        n_portcos=len(ingestions),
        n_rows_total=len(unified),
        n_fields=len(CANONICAL_FIELDS),
        n_anomalies=len(anomalies),
        as_of=date.today().isoformat(),
        lede=lede,
        mapping_blocks=mapping_blocks,
        anomaly_blocks=anomaly_blocks,
        normalized_csv=normalized_csv_path.name,
    )
    report_path.write_text(html, encoding="utf-8")

    return {
        "report_path": str(report_path),
        "normalized_csv_path": str(normalized_csv_path),
        "mapping_audit_path": str(mapping_audit_path),
        "anomalies_path": str(anomalies_path),
        "n_portcos": len(ingestions),
        "n_rows_normalized": int(len(unified)),
    }
