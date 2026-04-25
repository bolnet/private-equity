"""
dx_ingest — Multi-file CSV ingestion for the Decision-Optimization Diagnostic.

Loads one or more CSVs, matches them to entities in a vertical template,
joins them, computes the per-row $ outcome via the template's pure function,
runs validation gates, and stashes the joined dataframe in a session.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from fastmcp.exceptions import ToolError

from finance_mcp.dx.models import IngestReport, VerticalTemplate
from finance_mcp.dx.session import DiagnosticSession, save_session
from finance_mcp.dx.templates import get_template, match_template


def _match_file_to_entity(
    filename: str, template: VerticalTemplate
) -> Optional[str]:
    """Return the entity name whose filename patterns match this file, or None."""
    lower = os.path.basename(filename).lower()
    for entity in template.entities:
        if any(pat in lower for pat in entity.filename_patterns):
            return entity.name
    return None


def _join_entities(
    entity_dfs: dict, template: VerticalTemplate
) -> pd.DataFrame:
    """
    Join child entities into the parent using the template's join_keys.

    The first entity is treated as the root. Subsequent joins are left-joins
    so every parent row is preserved even without a child match (e.g. leads
    without policies still appear, with NaN in policy columns).
    """
    if not entity_dfs:
        raise ToolError("No entities to join — dx_ingest received zero files.")

    # Find the root: the entity that is a parent in join_keys but never a child.
    children = {child for child, _, _ in template.join_keys}
    parents = {parent for _, parent, _ in template.join_keys}
    roots = parents - children
    if not roots:
        # Fallback: use the first loaded entity
        root_name = next(iter(entity_dfs))
    else:
        root_name = next(iter(roots))

    if root_name not in entity_dfs:
        raise ToolError(
            f"Root entity '{root_name}' is required by template "
            f"'{template.id}' but was not provided."
        )

    joined = entity_dfs[root_name].copy()
    # Suffix root columns with nothing; child columns with their entity name
    # only if they collide.
    for child, parent, key in template.join_keys:
        if child not in entity_dfs:
            continue  # optional child entity missing
        if parent != root_name:
            # Chained joins not supported in MVP — templates with deep chains
            # should flatten to direct joins against the root.
            continue
        child_df = entity_dfs[child]
        if key not in joined.columns or key not in child_df.columns:
            raise ToolError(
                f"Join key '{key}' missing in {child} or {parent}. "
                f"Check your CSVs."
            )
        joined = joined.merge(
            child_df,
            how="left",
            on=key,
            suffixes=("", f"_{child}"),
        )

    return joined


def _run_validation_gates(
    joined: pd.DataFrame, template: VerticalTemplate
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return (gates_passed, gates_failed, warnings)."""
    gates = template.validation_gates
    passed: list[str] = []
    failed: list[str] = []
    warnings: list[str] = []

    # Months coverage gate
    if template.timestamp_column in joined.columns:
        ts = pd.to_datetime(joined[template.timestamp_column], errors="coerce", format="mixed")
        if ts.notna().any():
            months = (ts.max() - ts.min()).days / 30.44
            if months >= gates.min_months_coverage:
                passed.append(f"min_months_coverage (got {months:.1f})")
            else:
                failed.append(
                    f"min_months_coverage (got {months:.1f}, "
                    f"need {gates.min_months_coverage})"
                )
        else:
            warnings.append(
                f"timestamp column '{template.timestamp_column}' has no "
                f"parseable dates"
            )
    else:
        warnings.append(
            f"timestamp column '{template.timestamp_column}' not in joined data"
        )

    # Missing outcome gate
    null_rate = joined["_outcome_usd"].isna().mean() if "_outcome_usd" in joined else 1.0
    if null_rate <= gates.max_missing_pct_in_outcome:
        passed.append(f"max_missing_pct_in_outcome (got {null_rate:.2%})")
    else:
        failed.append(
            f"max_missing_pct_in_outcome (got {null_rate:.2%}, "
            f"need ≤ {gates.max_missing_pct_in_outcome:.2%})"
        )

    return tuple(passed), tuple(failed), tuple(warnings)


def dx_ingest(
    data_paths: list[str],
    vertical: str = "auto",
    portco_id: str = "unknown",
) -> dict:
    """
    Load multiple CSVs for a portfolio company, match them to a vertical
    template, join into a single dataframe, compute the per-row $ outcome,
    and validate.

    Args:
        data_paths: Absolute or relative paths to CSV files. File basenames
                    must contain a pattern that matches one of the template's
                    entity filename patterns (e.g., 'leads.csv' matches 'lead').
        vertical:   One of the registered template ids (e.g. 'insurance_b2c')
                    or 'auto' to auto-match based on filenames.
        portco_id:  Free-form label for this portfolio company. Used in
                    downstream reports.

    Returns:
        A plain dict mirroring IngestReport. Claude uses this to decide
        whether to proceed or ask the user to fix the data.
    """
    # Validate paths
    for p in data_paths:
        if not os.path.exists(p):
            raise ToolError(f"CSV file not found: {p}")

    # Template selection
    basenames = tuple(os.path.basename(p) for p in data_paths)
    if vertical == "auto":
        template_id, match_confidence = match_template(basenames)
        if template_id == "custom":
            raise ToolError(
                "Could not auto-match any vertical template to the provided "
                "files. Pass vertical='insurance_b2c' (or another known id)."
            )
    else:
        template_id = vertical
        match_confidence = 1.0
    template = get_template(template_id)

    # Load each file and match to an entity
    entity_dfs: dict = {}
    warnings: list[str] = []
    for path in data_paths:
        entity_name = _match_file_to_entity(path, template)
        if entity_name is None:
            warnings.append(
                f"file '{os.path.basename(path)}' did not match any entity "
                f"in template '{template_id}' — skipped"
            )
            continue
        df = pd.read_csv(path)
        entity_dfs[entity_name] = df

    if not entity_dfs:
        raise ToolError(
            f"No provided file matched an entity in template '{template_id}'. "
            f"Expected patterns: "
            f"{[e.filename_patterns for e in template.entities]}"
        )

    # Join
    joined = _join_entities(entity_dfs, template)

    # Compute $ outcome (pure function from template)
    try:
        outcome = template.compute_outcome(joined)
    except Exception as e:
        raise ToolError(
            f"Template '{template_id}' compute_outcome failed: {e}"
        ) from e
    joined = joined.assign(_outcome_usd=outcome.astype(float))

    # Validation gates
    passed, failed, gate_warnings = _run_validation_gates(joined, template)
    warnings.extend(gate_warnings)

    # Persist session
    session_id = f"dx_{uuid.uuid4().hex[:8]}"
    session = DiagnosticSession(
        session_id=session_id,
        portco_id=portco_id,
        template=template,
        joined=joined,
        created_at=datetime.now(timezone.utc),
    )
    save_session(session)

    # Months coverage for the report
    if template.timestamp_column in joined.columns:
        ts = pd.to_datetime(joined[template.timestamp_column], errors="coerce", format="mixed")
        if ts.notna().any():
            months = int((ts.max() - ts.min()).days / 30.44)
        else:
            months = 0
    else:
        months = 0

    report = IngestReport(
        portco_id=portco_id,
        template_id=template_id,
        template_match_confidence=round(match_confidence, 3),
        entities_loaded={k: len(v) for k, v in entity_dfs.items()},
        joined_rows=len(joined),
        months_coverage=months,
        null_rate_outcome=round(joined["_outcome_usd"].isna().mean(), 4),
        gates_passed=passed,
        gates_failed=failed,
        schema={c: str(t) for c, t in joined.dtypes.items()},
        session_id=session_id,
        warnings=tuple(warnings),
    )

    return {
        "portco_id": report.portco_id,
        "template_id": report.template_id,
        "template_match_confidence": report.template_match_confidence,
        "entities_loaded": report.entities_loaded,
        "joined_rows": report.joined_rows,
        "months_coverage": report.months_coverage,
        "null_rate_outcome": report.null_rate_outcome,
        "gates_passed": list(report.gates_passed),
        "gates_failed": list(report.gates_failed),
        "schema": report.schema,
        "session_id": report.session_id,
        "warnings": list(report.warnings),
    }
