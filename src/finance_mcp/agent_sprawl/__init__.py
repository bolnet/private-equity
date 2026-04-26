"""
agent_sprawl — Inventory the running PE-MCP agent fleet, flag zombie /
runaway-cost / misaligned agents, and produce a board-defendable pruning
report. Telemetry is modeled, not measured (see ``audit.py`` docstring).
"""
from finance_mcp.agent_sprawl.audit import audit_agents

__all__ = ["audit_agents"]
