"""
Decision-Optimization Diagnostic (DX) — Claude-native.

Six pandas-only MCP tools expose data slices Claude reasons over to find
cross-section decision-making blind spots in portfolio-company data.

No sklearn, scipy, SHAP, or causal-inference libs. Claude is the analyst;
pandas is the aggregation engine.

Public tools:
  • dx_ingest           — Multi-file CSV ingest + template match + validation
  • dx_segment_stats    — Pivot + rank segments by $ outcome
  • dx_time_stability   — Quarterly persistence check on a segment
  • dx_counterfactual   — Project $ impact of an alternative action
  • dx_evidence_rows    — Sample rows to ground narrative claims
  • dx_report           — Static HTML opportunity-map report
"""

from finance_mcp.dx.ingest import dx_ingest
from finance_mcp.dx.segment_stats import dx_segment_stats
from finance_mcp.dx.time_stability import dx_time_stability
from finance_mcp.dx.counterfactual import dx_counterfactual
from finance_mcp.dx.evidence import dx_evidence_rows
from finance_mcp.dx.memo import dx_memo
from finance_mcp.dx.report import dx_report

__all__ = [
    "dx_ingest",
    "dx_segment_stats",
    "dx_time_stability",
    "dx_counterfactual",
    "dx_evidence_rows",
    "dx_memo",
    "dx_report",
]
