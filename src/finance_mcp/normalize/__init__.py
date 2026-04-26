"""
normalize module — fold N portcos with heterogeneous chart-of-accounts
into a single canonical schema with per-cell provenance and cohort-level
anomaly detection.

The wedge: an operating partner gets P&Ls / loan tapes from N portcos every
month, all in different formats. They normalize them by hand into one
comparable view. This tool kills that ritual.
"""
from finance_mcp.normalize.normalize import normalize_portco

__all__ = ["normalize_portco"]
