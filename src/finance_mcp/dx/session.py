"""
In-memory session store for DX diagnostic runs.

A session = one ingested portco dataset + its template + its joined dataframe
with a precomputed `_outcome_usd` column. Downstream tools (dx_segment_stats,
dx_counterfactual, etc.) look up the session by `session_id` rather than
re-parsing CSVs on every call.

Sessions live only for the lifetime of the MCP server process. They are NOT
persisted. A crash or restart loses them — the user re-runs dx_ingest.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict

import pandas as pd

from finance_mcp.dx.models import VerticalTemplate


@dataclass(frozen=True)
class DiagnosticSession:
    session_id: str
    portco_id: str
    template: VerticalTemplate
    joined: pd.DataFrame  # includes _outcome_usd column
    created_at: datetime


_SESSIONS: Dict[str, DiagnosticSession] = {}


def save_session(session: DiagnosticSession) -> None:
    _SESSIONS[session.session_id] = session


def get_session(session_id: str) -> DiagnosticSession:
    if session_id not in _SESSIONS:
        known = ", ".join(sorted(_SESSIONS)) or "(none)"
        raise KeyError(
            f"No diagnostic session '{session_id}'. Known sessions: {known}. "
            f"Run dx_ingest first."
        )
    return _SESSIONS[session_id]


def list_sessions() -> tuple[str, ...]:
    return tuple(sorted(_SESSIONS))


def clear_sessions() -> None:
    """Clear all sessions. Used by tests."""
    _SESSIONS.clear()
