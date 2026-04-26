"""
Plan-drift detection — diff a frozen 100-day plan against the portco's
most recent SEC filing actuals to catch value-bleed at the Day-60
checkpoint, before the QBR.
"""
from finance_mcp.plan_drift.drift import track_plan_drift

__all__ = ["track_plan_drift"]
