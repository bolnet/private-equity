"""
Procurement module — Apollo-style cross-portco vendor benchmarking.

The wedge: Apollo's flagship value-creation lever is a 50-person procurement
team that surfaces price gaps for the same SKU across 12 portcos. This tool
productizes that for funds without Apollo's headcount.

Public-data demo: USAspending.gov treats every federal agency as a "portco"
buying the same service code (PSC) from a roster of vendors. Cross-agency
price-per-unit variance for the same vendor + service is the same shape of
question as cross-portco price-per-unit variance for the same SKU.
"""
from finance_mcp.procurement.benchmark import benchmark_vendors

__all__ = ["benchmark_vendors"]
