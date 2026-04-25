"""
bx_portco_rank — Rank one portco vs. its peers on a benchmark metric.

Pure pandas: sort the profiles dataframe by `metric`, compute this portco's
position, percentile, and corpus distribution (mean, median, p10, p90).

Claude uses this to produce LP-facing language like "Portco X sits in the
73rd percentile of allocation-archetype impact across your fund."
"""
from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
from fastmcp.exceptions import ToolError

from finance_mcp.bx.session import get_session


ALLOWED_METRICS = {
    "total_projected_impact_usd_annual",
    "pct_of_ebitda",
    "median_opportunity_usd",
    "top3_coverage_pct",
    "opportunity_count",
    "median_persistence_score",
    "median_difficulty",
    "allocation_impact_usd",
    "pricing_impact_usd",
    "routing_impact_usd",
    "timing_impact_usd",
    "selection_impact_usd",
}


def bx_portco_rank(
    corpus_id: str,
    portco_id: str,
    metric: str = "total_projected_impact_usd_annual",
    order: Literal["desc", "asc"] = "desc",
) -> dict:
    """
    Rank a portco within the corpus on a metric.

    Args:
        corpus_id: From a prior bx_ingest_corpus call.
        portco_id: Which portco to rank.
        metric:    One of the ALLOWED_METRICS.
        order:     'desc' = higher is better (default). 'asc' = lower is better.

    Returns:
        dict with rank, rank_total, percentile (0-100), value, and corpus
        distribution stats (mean, median, p10, p90).
    """
    if metric not in ALLOWED_METRICS:
        raise ToolError(
            f"metric must be one of {sorted(ALLOWED_METRICS)}, got {metric!r}"
        )
    session = get_session(corpus_id)
    df = session.portco_profiles_df

    if df.empty:
        raise ToolError("Corpus is empty.")
    if portco_id not in df["portco_id"].values:
        raise ToolError(
            f"portco_id {portco_id!r} not in corpus. "
            f"Known: {sorted(df['portco_id'].unique().tolist())}"
        )

    values = df[metric].astype(float).to_numpy()
    target = float(df.loc[df["portco_id"] == portco_id, metric].iloc[0])

    # Rank (1-based, dense-ish). For 'desc', larger values get lower rank numbers.
    if order == "desc":
        rank = int((values > target).sum() + 1)
    else:
        rank = int((values < target).sum() + 1)
    rank_total = int(len(values))

    # Percentile: the fraction of the corpus this portco is above.
    # For desc metrics, higher value -> higher percentile.
    if order == "desc":
        better_count = float((values < target).sum())
    else:
        better_count = float((values > target).sum())
    percentile = round(better_count / max(rank_total - 1, 1) * 100.0, 1) if rank_total > 1 else 50.0

    return {
        "corpus_id": corpus_id,
        "portco_id": portco_id,
        "metric": metric,
        "order": order,
        "value": round(target, 4),
        "rank": rank,
        "rank_total": rank_total,
        "percentile": percentile,
        "corpus_mean": round(float(np.mean(values)), 4),
        "corpus_median": round(float(np.median(values)), 4),
        "corpus_p10": round(float(np.percentile(values, 10)), 4),
        "corpus_p90": round(float(np.percentile(values, 90)), 4),
    }
