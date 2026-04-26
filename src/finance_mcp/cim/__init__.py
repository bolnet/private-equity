"""
CIM Red-Flag Extractor — runs heuristic diligence flags over a real SEC
filing (10-K / S-1 / S-4) and emits a board-grade red-flag report with
section citations.
"""
from finance_mcp.cim.analyze import cim_analyze

__all__ = ["cim_analyze"]
