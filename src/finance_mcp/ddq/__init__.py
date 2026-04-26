"""
DDQ-respond module — generate first-draft responses to a fund-manager
DDQ (Due-Diligence Questionnaire) by retrieving from the fund's existing
AI-related artifacts in `finance_output/`, templating an answer per
ILPA-shaped question, and scoring cross-answer consistency.

The wedge: ILPA DDQ v2.0 (Q1 2026) added new AI governance / data /
risk sections; funds are answering them inconsistently across vintages.
The first GP that ships a consistency layer wins the next allocation
cycle.
"""
from finance_mcp.ddq.respond import ddq_respond

__all__ = ["ddq_respond"]
