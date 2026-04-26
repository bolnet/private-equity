"""
Sensitivity-table math for the exit-proof pack.

Three sensitivity bands map to the language a buyer's AI diligence team
will actually use in its read-out:

  - conservative — 50% of modeled impact (haircut for execution shortfall,
    persistence decay, and counterfactual softness)
  - base        — 100% of modeled impact (the seller's headline claim)
  - aggressive  — 130% of modeled impact (upside if rollout completes
    inside the underwriting period and adjacent cohorts catch the same
    pattern)

These bands are deterministic constants, not opinions, so the buyer can
re-derive them and disagree by varying the multipliers — which is exactly
how a defensible disclosure should work.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final


# Sensitivity multipliers (frozen constants — buyer can re-derive)
CONSERVATIVE_MULTIPLIER: Final[float] = 0.50
BASE_MULTIPLIER: Final[float] = 1.00
AGGRESSIVE_MULTIPLIER: Final[float] = 1.30


@dataclass(frozen=True)
class SensitivityRow:
    """A single claim's value under all three sensitivity bands."""

    claim_id: str
    conservative_usd: float
    base_usd: float
    aggressive_usd: float

    @property
    def spread_usd(self) -> float:
        """Aggressive minus conservative — the disclosed range width."""
        return self.aggressive_usd - self.conservative_usd


@dataclass(frozen=True)
class SensitivityTable:
    """Aggregate sensitivity across all claims in the pack."""

    rows: tuple[SensitivityRow, ...]
    total_conservative_usd: float
    total_base_usd: float
    total_aggressive_usd: float

    @property
    def range_usd(self) -> tuple[float, float]:
        return (self.total_conservative_usd, self.total_aggressive_usd)


def compute_row(claim_id: str, base_impact_usd: float) -> SensitivityRow:
    """Apply the three multipliers to a single claim's base impact."""
    base = float(base_impact_usd or 0.0)
    return SensitivityRow(
        claim_id=claim_id,
        conservative_usd=base * CONSERVATIVE_MULTIPLIER,
        base_usd=base * BASE_MULTIPLIER,
        aggressive_usd=base * AGGRESSIVE_MULTIPLIER,
    )


def build_table(claims: list[tuple[str, float]]) -> SensitivityTable:
    """Build a sensitivity table from a list of (claim_id, base_impact_usd)."""
    rows = tuple(compute_row(cid, impact) for cid, impact in claims)
    total_c = sum(r.conservative_usd for r in rows)
    total_b = sum(r.base_usd for r in rows)
    total_a = sum(r.aggressive_usd for r in rows)
    return SensitivityTable(
        rows=rows,
        total_conservative_usd=total_c,
        total_base_usd=total_b,
        total_aggressive_usd=total_a,
    )
