"""
bx_peer_group — Find the N most similar portcos to a reference portco.

Similarity is computed on a small fixed profile vector: per-archetype
$ share of total impact + normalized total impact + pct_of_ebitda.
Cosine similarity on the normalized vector.

Why cosine and not Euclidean? We care about *shape of the profile* (is this
portco mostly allocation-driven? mostly pricing?) not absolute scale.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from fastmcp.exceptions import ToolError

from finance_mcp.bx.session import get_session


_PROFILE_DIMS = (
    "allocation_impact_usd",
    "pricing_impact_usd",
    "routing_impact_usd",
    "timing_impact_usd",
    "selection_impact_usd",
    "pct_of_ebitda",
    "median_persistence_score",
)


def _unit_vector(row: pd.Series) -> np.ndarray:
    """Normalize a profile row to a unit vector for cosine similarity."""
    vec = np.array([float(row[d]) for d in _PROFILE_DIMS])
    norm = np.linalg.norm(vec)
    if norm == 0.0:
        return vec
    return vec / norm


def _top_archetype(row: pd.Series) -> str:
    """Which archetype contributes the most to this portco's impact?"""
    amounts = {
        "allocation": float(row["allocation_impact_usd"]),
        "pricing": float(row["pricing_impact_usd"]),
        "routing": float(row["routing_impact_usd"]),
        "timing": float(row["timing_impact_usd"]),
        "selection": float(row["selection_impact_usd"]),
    }
    top = max(amounts, key=amounts.get)
    return top if amounts[top] > 0 else "none"


def bx_peer_group(
    corpus_id: str,
    portco_id: str,
    top_n: int = 5,
) -> dict:
    """
    Find the top-N most similar portcos to `portco_id` within the corpus.

    Returns:
        dict with reference_portco + peers list (descending similarity).
    """
    session = get_session(corpus_id)
    df = session.portco_profiles_df

    if portco_id not in df["portco_id"].values:
        raise ToolError(
            f"portco_id {portco_id!r} not in corpus. "
            f"Known: {sorted(df['portco_id'].unique().tolist())}"
        )
    if len(df) < 2:
        return {
            "corpus_id": corpus_id,
            "reference_portco_id": portco_id,
            "peers": [],
            "note": "Corpus has fewer than 2 portcos; peer group not meaningful.",
        }

    ref_row = df.loc[df["portco_id"] == portco_id].iloc[0]
    ref_vec = _unit_vector(ref_row)
    ref_top = _top_archetype(ref_row)

    peers = []
    for _, row in df.iterrows():
        if row["portco_id"] == portco_id:
            continue
        vec = _unit_vector(row)
        denom = float(np.linalg.norm(ref_vec) * np.linalg.norm(vec))
        similarity = float(np.dot(ref_vec, vec)) if denom > 0 else 0.0
        peers.append(
            {
                "portco_id": str(row["portco_id"]),
                "vertical": str(row["vertical"]),
                "similarity_score": round(similarity, 4),
                "shared_top_archetype": (
                    _top_archetype(row)
                    if _top_archetype(row) == ref_top
                    else "—"
                ),
                "top_archetype": _top_archetype(row),
            }
        )
    peers.sort(key=lambda p: p["similarity_score"], reverse=True)

    return {
        "corpus_id": corpus_id,
        "reference_portco_id": portco_id,
        "reference_top_archetype": ref_top,
        "peers": peers[: max(1, int(top_n))],
    }
