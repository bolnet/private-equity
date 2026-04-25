"""
dx_segment_stats — Rank decision × segment cells by $ outcome.

Pure pandas: group the session's joined dataframe by the decision columns,
aggregate the _outcome_usd column, annualize, and return the top-K segments.

This is the primitive Claude uses to SEE the cross-section patterns that
aggregate-only dashboards miss.
"""
from __future__ import annotations

from typing import Literal

import pandas as pd
from fastmcp.exceptions import ToolError

from finance_mcp.dx.session import get_session


def _annualization_factor(df: pd.DataFrame, timestamp_col: str) -> float:
    """Months of data ÷ 12 → scalar to convert totals to annual."""
    if timestamp_col not in df.columns:
        return 1.0
    ts = pd.to_datetime(df[timestamp_col], errors="coerce", format="mixed")
    if not ts.notna().any():
        return 1.0
    months = max((ts.max() - ts.min()).days / 30.44, 1.0)
    return 12.0 / months


def dx_segment_stats(
    session_id: str,
    decision_cols: list[str],
    min_segment_n: int = 30,
    top_k: int = 50,
    rank_by: Literal["worst_total", "best_total", "worst_mean", "abs_total"] = "worst_total",
) -> dict:
    """
    Group the session dataframe by the given decision columns and return
    the top-K segments ranked by $ outcome.

    Args:
        session_id:       id from a prior dx_ingest call.
        decision_cols:    1–4 column names to cross. Typical: ['source','state'].
        min_segment_n:    Drop segments with fewer than this many rows.
        top_k:            Max number of segments to return.
        rank_by:          'worst_total' (losses first), 'best_total' (gains first),
                          'worst_mean' (worst per-unit), 'abs_total' (biggest either way).

    Returns:
        dict with keys:
          session_id, decision_cols, rank_by, total_rows, segments (list of SegmentStat dicts)
    """
    session = get_session(session_id)
    df = session.joined

    if not decision_cols:
        raise ToolError("dx_segment_stats requires at least one decision column.")
    missing = [c for c in decision_cols if c not in df.columns]
    if missing:
        raise ToolError(
            f"Decision columns not in data: {missing}. "
            f"Available columns: {sorted(df.columns)[:30]}..."
        )

    if "_outcome_usd" not in df.columns:
        raise ToolError(
            "Session dataframe is missing _outcome_usd column. "
            "This should not happen — re-run dx_ingest."
        )

    ann = _annualization_factor(df, session.template.timestamp_column)

    # Group & aggregate — pandas handles NaN keys by default; drop them.
    grouped = (
        df.dropna(subset=decision_cols)
        .groupby(decision_cols, dropna=True, observed=True)["_outcome_usd"]
        .agg(n="size", mean="mean", std="std", total="sum")
        .reset_index()
    )

    # Filter by min sample
    grouped = grouped[grouped["n"] >= min_segment_n].copy()
    if grouped.empty:
        return {
            "session_id": session_id,
            "decision_cols": list(decision_cols),
            "rank_by": rank_by,
            "total_rows": 0,
            "segments": [],
            "note": f"No segment had >= {min_segment_n} rows",
        }

    # Volume + negative-outcome denominators
    total_volume = int(df.shape[0])
    total_negative = float(df.loc[df["_outcome_usd"] < 0, "_outcome_usd"].sum())

    grouped["annual_total_usd"] = grouped["total"] * ann
    grouped["pct_of_volume"] = grouped["n"] / max(total_volume, 1)
    grouped["pct_of_negative_outcome"] = grouped["total"].where(
        grouped["total"] < 0, 0.0
    ) / (total_negative if total_negative != 0 else 1.0)

    # Rank
    if rank_by == "worst_total":
        grouped = grouped.sort_values("annual_total_usd", ascending=True)
    elif rank_by == "best_total":
        grouped = grouped.sort_values("annual_total_usd", ascending=False)
    elif rank_by == "worst_mean":
        grouped = grouped.sort_values("mean", ascending=True)
    elif rank_by == "abs_total":
        grouped = grouped.reindex(
            grouped["annual_total_usd"].abs().sort_values(ascending=False).index
        )
    else:
        raise ToolError(f"Unknown rank_by='{rank_by}'")

    head = grouped.head(top_k)

    segments: list[dict] = []
    for _, row in head.iterrows():
        seg_dict = {c: _json_safe(row[c]) for c in decision_cols}
        segments.append(
            {
                "segment": seg_dict,
                "n": int(row["n"]),
                "outcome_mean": float(row["mean"]),
                "outcome_std": float(row["std"]) if pd.notna(row["std"]) else 0.0,
                "outcome_total_usd_annual": float(row["annual_total_usd"]),
                "pct_of_volume": float(row["pct_of_volume"]),
                "pct_of_negative_outcome": float(row["pct_of_negative_outcome"]),
            }
        )

    return {
        "session_id": session_id,
        "decision_cols": list(decision_cols),
        "rank_by": rank_by,
        "total_rows": total_volume,
        "annualization_factor": round(ann, 4),
        "segments": segments,
    }


def _json_safe(v):
    """Convert pandas/numpy scalars to JSON-safe Python primitives."""
    if pd.isna(v):
        return None
    if hasattr(v, "item"):
        return v.item()
    return v
