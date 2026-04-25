"""
bx_archetype_index — Aggregate $ distribution per archetype across the corpus.

Answers: "What does a typical allocation-archetype opportunity look like in
our fund? What's the 90th-percentile pricing-archetype impact? How rare is
a material selection-archetype finding?"
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from fastmcp.exceptions import ToolError

from finance_mcp.bx.session import get_session


ARCHETYPES = ("allocation", "pricing", "routing", "timing", "selection")


def bx_archetype_index(corpus_id: str) -> dict:
    """
    Compute per-archetype distribution across all opportunities in the corpus.

    Returns:
        dict with one entry per archetype. Each entry has:
          • archetype, portco_count_with_archetype, opportunity_count
          • median_impact_usd, p10_impact_usd, p90_impact_usd
          • total_impact_usd
          • share_of_corpus_total_pct
    """
    session = get_session(corpus_id)
    opps = session.opportunities_df

    if opps.empty:
        return {
            "corpus_id": corpus_id,
            "archetype_stats": [],
            "corpus_total_impact_usd": 0.0,
        }

    corpus_total = float(opps["projected_impact_usd_annual"].sum())
    stats: list[dict] = []

    for arche in ARCHETYPES:
        sub = opps[opps["archetype"] == arche]
        if sub.empty:
            stats.append(
                {
                    "archetype": arche,
                    "portco_count_with_archetype": 0,
                    "opportunity_count": 0,
                    "median_impact_usd": 0.0,
                    "p10_impact_usd": 0.0,
                    "p90_impact_usd": 0.0,
                    "total_impact_usd": 0.0,
                    "share_of_corpus_total_pct": 0.0,
                }
            )
            continue
        vals = sub["projected_impact_usd_annual"].astype(float).to_numpy()
        total = float(vals.sum())
        stats.append(
            {
                "archetype": arche,
                "portco_count_with_archetype": int(sub["portco_id"].nunique()),
                "opportunity_count": int(len(sub)),
                "median_impact_usd": round(float(np.median(vals)), 2),
                "p10_impact_usd": round(float(np.percentile(vals, 10)), 2),
                "p90_impact_usd": round(float(np.percentile(vals, 90)), 2),
                "total_impact_usd": round(total, 2),
                "share_of_corpus_total_pct": round(
                    total / corpus_total * 100.0 if corpus_total else 0.0, 2
                ),
            }
        )

    return {
        "corpus_id": corpus_id,
        "corpus_total_impact_usd": round(corpus_total, 2),
        "archetype_stats": stats,
    }
