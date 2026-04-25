"""
dx_evidence_rows — Return raw rows for narrative grounding.

When Claude writes "Over 36 months, 3 state × source cells consumed 27% of
lead spend…" the claim has to cite specific rows. This tool returns a small
sample of the actual rows backing the claim.

Keeps narrative from drifting into hallucination: every quoted number or
row id in the memo can be traced back to a dx_evidence_rows response.
"""
from __future__ import annotations

from typing import Literal

import pandas as pd
from fastmcp.exceptions import ToolError

from finance_mcp.dx.session import get_session


def dx_evidence_rows(
    session_id: str,
    segment_filter: dict,
    limit: int = 20,
    sort_by: Literal["outcome_asc", "outcome_desc", "recent"] = "outcome_asc",
) -> dict:
    """
    Return a sample of rows matching the segment filter.

    Args:
        session_id:      From dx_ingest.
        segment_filter:  {col: value, ...} equality filter.
        limit:           Max rows to return (capped at 100 for safety).
        sort_by:         'outcome_asc' (worst first), 'outcome_desc' (best first),
                         'recent' (latest timestamp first).

    Returns:
        dict with segment filter, total matched rows, and a list of
        evidence_rows (row_id + data dict).
    """
    session = get_session(session_id)
    df = session.joined

    limit = max(1, min(int(limit), 100))

    mask = pd.Series(True, index=df.index)
    for col, value in segment_filter.items():
        if col not in df.columns:
            raise ToolError(f"Segment filter column '{col}' not in data.")
        mask &= df[col] == value

    seg = df[mask]
    total_matched = len(seg)
    if total_matched == 0:
        return {
            "session_id": session_id,
            "segment": segment_filter,
            "total_matched": 0,
            "rows_returned": 0,
            "sort_by": sort_by,
            "evidence_rows": [],
        }

    if sort_by == "outcome_asc":
        seg = seg.sort_values("_outcome_usd", ascending=True, na_position="last")
    elif sort_by == "outcome_desc":
        seg = seg.sort_values("_outcome_usd", ascending=False, na_position="last")
    elif sort_by == "recent":
        ts_col = session.template.timestamp_column
        if ts_col in seg.columns:
            seg = seg.assign(
                _ts=pd.to_datetime(seg[ts_col], errors="coerce", format="mixed")
            ).sort_values("_ts", ascending=False, na_position="last").drop(columns=["_ts"])
    else:
        raise ToolError(f"Unknown sort_by='{sort_by}'")

    head = seg.head(limit)
    evidence: list[dict] = []
    for row_id, row in head.iterrows():
        data = {}
        for col in head.columns:
            v = row[col]
            if pd.isna(v):
                data[col] = None
            elif hasattr(v, "item"):
                data[col] = v.item()
            elif isinstance(v, pd.Timestamp):
                data[col] = v.isoformat()
            else:
                data[col] = v
        evidence.append({"row_id": int(row_id), "data": data})

    return {
        "session_id": session_id,
        "segment": segment_filter,
        "total_matched": int(total_matched),
        "rows_returned": len(evidence),
        "sort_by": sort_by,
        "evidence_rows": evidence,
    }
