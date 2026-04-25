"""
Benchmarking (BX) — cross-portco + within-portco time-series analysis
for Decision-Optimization Diagnostic outputs.

BX consumes OpportunityMap JSON sidecars (produced by dx_report) and
produces two kinds of benchmark views:

  WITHIN-FUND (Option A):
    • bx_ingest_corpus    — load N OpportunityMap JSONs
    • bx_portco_rank      — rank this portco vs. peers on a metric
    • bx_archetype_index  — aggregate $ distribution per archetype
    • bx_peer_group       — find similar portcos by profile vector
    • bx_report           — static HTML benchmark report

  WITHIN-PORTCO TIME-SERIES (Option B):
    • bx_snapshot         — stash a dated snapshot
    • bx_trend            — per-quarter trend of identified $
    • bx_delta            — what changed since a prior snapshot

All tools are pandas-only and Claude-native — no new dependencies beyond
what DX already uses. Benchmark sessions live in-memory per run, same as
DX diagnostic sessions.
"""

from finance_mcp.bx.ingest_corpus import bx_ingest_corpus
from finance_mcp.bx.rank import bx_portco_rank
from finance_mcp.bx.archetype_index import bx_archetype_index
from finance_mcp.bx.peer_group import bx_peer_group
from finance_mcp.bx.report import bx_report
from finance_mcp.bx.snapshot import bx_snapshot
from finance_mcp.bx.trend import bx_trend
from finance_mcp.bx.delta import bx_delta

__all__ = [
    # Option A — within-fund
    "bx_ingest_corpus",
    "bx_portco_rank",
    "bx_archetype_index",
    "bx_peer_group",
    "bx_report",
    # Option B — within-portco time-series
    "bx_snapshot",
    "bx_trend",
    "bx_delta",
]
