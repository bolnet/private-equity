"""
Data models for the benchmarking module.

All frozen dataclasses — benchmark sessions are built by chaining tool calls
so mutation would break the audit trail.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


BenchmarkMetric = Literal[
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
]


@dataclass(frozen=True)
class PortcoProfile:
    """One portco's aggregated metrics — derived from its OpportunityMap."""

    portco_id: str
    vertical: str
    ebitda_baseline_usd: float
    as_of: str
    total_projected_impact_usd_annual: float
    pct_of_ebitda: float
    opportunity_count: int
    median_opportunity_usd: float
    top3_coverage_pct: float
    median_persistence_score: float
    median_difficulty: float
    # Per-archetype impact totals (absent archetypes -> 0.0)
    allocation_impact_usd: float
    pricing_impact_usd: float
    routing_impact_usd: float
    timing_impact_usd: float
    selection_impact_usd: float


@dataclass(frozen=True)
class RankResult:
    """One portco's rank on a single metric within a corpus."""

    portco_id: str
    metric: BenchmarkMetric
    value: float
    rank: int
    rank_total: int
    percentile: float
    corpus_mean: float
    corpus_median: float
    corpus_p10: float
    corpus_p90: float


@dataclass(frozen=True)
class ArchetypeStat:
    """One archetype's distribution across the corpus."""

    archetype: str
    portco_count_with_archetype: int
    median_impact_usd: float
    p10_impact_usd: float
    p90_impact_usd: float
    total_impact_usd: float


@dataclass(frozen=True)
class PeerMatch:
    """One peer portco + similarity score."""

    portco_id: str
    vertical: str
    similarity_score: float  # 0.0 (different) to 1.0 (identical)
    shared_top_archetype: str


@dataclass(frozen=True)
class DeltaReport:
    """Change in a portco's metrics between two snapshots."""

    portco_id: str
    from_date: str
    to_date: str
    days_elapsed: int
    opportunities_closed: int  # present at from_date, absent at to_date
    opportunities_new: int  # absent at from_date, present at to_date
    opportunities_persistent: int
    delta_total_impact_usd: float
    delta_pct_of_ebitda: float
