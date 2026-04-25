"""
Data models for the Decision-Optimization Diagnostic.

All models are frozen dataclasses — immutability is load-bearing here because
the analysis pipeline builds up a diagnostic session by passing these through
a chain of tools. Mutations would make the audit trail unreliable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Literal, Optional

import pandas as pd


Archetype = Literal["allocation", "pricing", "routing", "timing", "selection"]


# ---------------------------------------------------------------------------
# Vertical template — declarative spec for "what do the CSVs mean"
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntitySpec:
    """One CSV file in a vertical template."""

    name: str
    filename_patterns: tuple[str, ...]  # e.g. ("leads", "lead")
    primary_key: str
    expected_columns: tuple[str, ...]


@dataclass(frozen=True)
class ArchetypeSpec:
    """One archetype (allocation / pricing / etc.) within a template."""

    archetype: Archetype
    decision_columns: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class ValidationGates:
    """Hard data-quality thresholds. Ingestion fails if any gate fails."""

    min_rows_per_segment: int = 30
    min_months_coverage: int = 12
    max_missing_pct_in_outcome: float = 0.05


@dataclass(frozen=True)
class VerticalTemplate:
    """
    A vertical template tells the diagnostic what the CSVs mean and how
    to compute the per-row $ outcome.

    `compute_outcome` is a pure function: (joined_df) -> pd.Series aligned
    to the joined dataframe index.  Executes inside dx_ingest after join.
    """

    id: str
    version: str
    description: str
    entities: tuple[EntitySpec, ...]
    join_keys: tuple[tuple[str, str, str], ...]
    # (child_entity, parent_entity, key_column)
    timestamp_column: str  # column in joined df used for time-stability
    compute_outcome: Callable[[pd.DataFrame], pd.Series]
    archetypes: tuple[ArchetypeSpec, ...]
    validation_gates: ValidationGates = field(default_factory=ValidationGates)


# ---------------------------------------------------------------------------
# Ingestion output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IngestReport:
    """Returned by dx_ingest. Claude inspects this before proceeding."""

    portco_id: str
    template_id: str
    template_match_confidence: float
    entities_loaded: dict  # entity_name -> row_count
    joined_rows: int
    months_coverage: int
    null_rate_outcome: float
    gates_passed: tuple[str, ...]
    gates_failed: tuple[str, ...]
    schema: dict  # column -> dtype
    session_id: str  # used by downstream tools to reference the in-memory df
    warnings: tuple[str, ...]


# ---------------------------------------------------------------------------
# Analysis outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SegmentStat:
    """One row of dx_segment_stats output."""

    segment: dict  # {column: value, ...}
    n: int
    outcome_mean: float
    outcome_std: float
    outcome_total_usd: float
    pct_of_volume: float
    pct_of_negative_outcome: float


@dataclass(frozen=True)
class TimeStabilityReport:
    """dx_time_stability output."""

    segment: dict
    quarters: tuple[str, ...]
    quarterly_outcome_mean: tuple[float, ...]
    quarterly_outcome_total_usd: tuple[float, ...]
    persistence_quarters: int
    total_quarters: int
    persistence_score: float  # persistence_quarters / total_quarters


@dataclass(frozen=True)
class CounterfactualReport:
    """dx_counterfactual output."""

    segment: dict
    action: str
    action_params: dict
    current_outcome_usd_annual: float
    projected_outcome_usd_annual: float
    projected_impact_usd_annual: float
    rows_affected: int
    rows_retained: int


@dataclass(frozen=True)
class EvidenceRow:
    """A single raw-data row for narrative grounding."""

    row_id: int
    data: dict  # column -> value


# ---------------------------------------------------------------------------
# Final opportunity map (assembled by Claude agent from tool outputs)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Opportunity:
    id: str
    archetype: Archetype
    decision_cols: tuple[str, ...]
    segment: dict
    n: int
    current_outcome_usd_annual: float
    projected_outcome_usd_annual: float
    projected_impact_usd_annual: float
    persistence_quarters_out_of_total: tuple[int, int]
    difficulty_score_1_to_5: int
    time_to_implement_weeks: int
    recommendation: str
    evidence_row_ids: tuple[int, ...]
    narrative_board: str = ""
    narrative_operator: str = ""


@dataclass(frozen=True)
class OpportunityMap:
    portco_id: str
    as_of: date
    vertical: str
    ebitda_baseline_usd: float
    opportunities: tuple[Opportunity, ...]
    total_projected_impact_usd_annual: float
    generated_at: datetime
