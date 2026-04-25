"""
In-memory benchmark session store.

Same pattern as finance_mcp.dx.session — a `bx_ingest_corpus` call produces
a BenchmarkSession that downstream tools look up by corpus_id. Sessions are
process-local; no persistence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict

import pandas as pd


@dataclass(frozen=True)
class BenchmarkSession:
    corpus_id: str
    portco_count: int
    portco_profiles_df: pd.DataFrame  # one row per portco, one col per metric
    opportunities_df: pd.DataFrame  # long format: one row per opportunity
    created_at: datetime
    source_json_paths: tuple[str, ...]


_SESSIONS: Dict[str, BenchmarkSession] = {}


def save_session(session: BenchmarkSession) -> None:
    _SESSIONS[session.corpus_id] = session


def get_session(corpus_id: str) -> BenchmarkSession:
    if corpus_id not in _SESSIONS:
        known = ", ".join(sorted(_SESSIONS)) or "(none)"
        raise KeyError(
            f"No benchmark session '{corpus_id}'. Known: {known}. "
            f"Run bx_ingest_corpus first."
        )
    return _SESSIONS[corpus_id]


def list_sessions() -> tuple[str, ...]:
    return tuple(sorted(_SESSIONS))


def clear_sessions() -> None:
    """Clear all benchmark sessions. Used by tests."""
    _SESSIONS.clear()
