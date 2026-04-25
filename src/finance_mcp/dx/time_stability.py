"""
dx_time_stability — Is a segment persistently bad/good across quarters?

Replaces bootstrap confidence intervals with a simple, auditable signal:
how many quarters (of the available coverage) does this segment rank in
the bottom / top outcome tier?

Claude uses this to filter out transient or regime-specific findings
before recommending action.
"""
from __future__ import annotations

from typing import Literal

import pandas as pd
from fastmcp.exceptions import ToolError

from finance_mcp.dx.session import get_session


def _filter_to_segment(df: pd.DataFrame, segment_filter: dict) -> pd.DataFrame:
    """Apply an equality filter {col: value, ...} to a dataframe."""
    mask = pd.Series(True, index=df.index)
    for col, value in segment_filter.items():
        if col not in df.columns:
            raise ToolError(f"Segment filter column '{col}' not in data.")
        mask &= df[col] == value
    return df[mask]


def dx_time_stability(
    session_id: str,
    segment_filter: dict,
    direction: Literal["negative", "positive"] = "negative",
) -> dict:
    """
    For the given segment, compute per-quarter outcome stats.

    Persistence = number of quarters in which this segment's mean outcome
    is on the `direction` side of zero. For `direction="negative"`, a high
    persistence score means "losses have been consistent every quarter."

    Returns:
      session_id, segment, quarters (list), quarterly_outcome_mean,
      quarterly_outcome_total_usd, persistence_quarters, total_quarters,
      persistence_score (0.0–1.0).
    """
    session = get_session(session_id)
    df = session.joined
    timestamp_col = session.template.timestamp_column

    if timestamp_col not in df.columns:
        raise ToolError(
            f"Timestamp column '{timestamp_col}' not in joined data."
        )

    seg = _filter_to_segment(df, segment_filter)
    if seg.empty:
        return {
            "session_id": session_id,
            "segment": segment_filter,
            "quarters": [],
            "quarterly_outcome_mean": [],
            "quarterly_outcome_total_usd": [],
            "persistence_quarters": 0,
            "total_quarters": 0,
            "persistence_score": 0.0,
            "note": "Segment filter matched zero rows.",
        }

    ts = pd.to_datetime(seg[timestamp_col], errors="coerce", format="mixed")
    seg = seg.assign(_quarter=ts.dt.to_period("Q").astype(str))
    seg = seg[seg["_quarter"].notna()]
    if seg.empty:
        return {
            "session_id": session_id,
            "segment": segment_filter,
            "quarters": [],
            "quarterly_outcome_mean": [],
            "quarterly_outcome_total_usd": [],
            "persistence_quarters": 0,
            "total_quarters": 0,
            "persistence_score": 0.0,
            "note": "No parseable timestamps within the segment.",
        }

    q = (
        seg.groupby("_quarter", observed=True)["_outcome_usd"]
        .agg(["mean", "sum"])
        .reset_index()
        .sort_values("_quarter")
    )

    quarters = q["_quarter"].tolist()
    means = [float(v) for v in q["mean"].tolist()]
    totals = [float(v) for v in q["sum"].tolist()]

    if direction == "negative":
        persistent = sum(1 for m in means if m < 0)
    else:
        persistent = sum(1 for m in means if m > 0)

    total_q = len(quarters)
    return {
        "session_id": session_id,
        "segment": segment_filter,
        "direction": direction,
        "quarters": quarters,
        "quarterly_outcome_mean": means,
        "quarterly_outcome_total_usd": totals,
        "persistence_quarters": persistent,
        "total_quarters": total_q,
        "persistence_score": round(persistent / total_q, 3) if total_q else 0.0,
    }
