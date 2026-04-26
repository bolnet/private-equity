"""Smoke test for normalize_portco — runs against the 3 real demo portcos."""
import json
import os
from pathlib import Path

import pytest

from finance_mcp.normalize import normalize_portco


_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def cwd_repo():
    """The tool resolves portco paths relative to cwd; pin it to the repo root."""
    prev = Path.cwd()
    os.chdir(_REPO)
    try:
        yield _REPO
    finally:
        os.chdir(prev)


@pytest.mark.integration
def test_normalize_three_real_portcos(cwd_repo, capsys):
    result = normalize_portco(
        portco_csv_paths=[
            "demo/regional_lenders/midwest_lender/",
            "demo/yasserh_mortgages/",
            "demo/hmda_states/ga/",
        ],
        portco_ids=["midwest_lender", "MortgageCo", "HMDA_GA"],
        output_filename="normalize_smoketest",
    )

    print("\n=== normalize_portco result ===")
    for k, v in result.items():
        print(f"{k}: {v}")

    assert result["n_portcos"] == 3
    assert result["n_rows_normalized"] > 0
    assert Path(result["report_path"]).exists()
    assert Path(result["normalized_csv_path"]).exists()
    assert Path(result["mapping_audit_path"]).exists()
    assert Path(result["anomalies_path"]).exists()

    audit = json.loads(Path(result["mapping_audit_path"]).read_text())
    print(f"\n=== mapping audit summary ===")
    print(f"n_portcos: {audit['n_portcos']}")
    print(f"n_rows_total: {audit['n_rows_total']}")
    for portco in audit["portcos"]:
        print(f"\n--- {portco['portco_id']} ({portco['n_rows']:,} rows) ---")
        print(f"missing_required: {portco['missing_required']}")
        for m in portco["column_mappings"]:
            method = m["match_method"]
            score = m["match_score"]
            tgt = m["canonical_field"] or "(unmapped)"
            print(f"  {m['source_column']!r:30s} -> {tgt:15s} [{method}, {score:.2f}]")

    anom = json.loads(Path(result["anomalies_path"]).read_text())
    print(f"\n=== anomalies ({anom['n_anomalies']}) ===")
    for a in anom["anomalies"]:
        print(f"  [{a['severity']}] {a['kind']} on {a['canonical_field']} for {a['portco_id']}")
        print(f"     {a['detail']}")

    portcos_with_required = [
        p for p in audit["portcos"] if not p["missing_required"]
    ]
    assert len(portcos_with_required) >= 2

    assert anom["n_anomalies"] >= 1, (
        "expected >= 1 cohort anomaly across the 3 real portcos"
    )
