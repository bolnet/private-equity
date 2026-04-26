"""
Private-Equity MCP Server — FastMCP stdio transport.

Registers the DX (Decision-Optimization Diagnostic) and BX (Cross-Portco
Benchmarking) tool families. Extracted from the broader claude-finance
repo so PE workflows can ship and version independently.

Run:  python -m finance_mcp.server   (stdio transport, launched by Claude Code)
Log:  all debug output goes to stderr (stdout is the MCP protocol channel)
"""
import importlib
import sys

from fastmcp import FastMCP

from finance_mcp.bx import (
    bx_archetype_index,
    bx_delta,
    bx_ingest_corpus,
    bx_peer_group,
    bx_portco_rank,
    bx_report,
    bx_snapshot,
    bx_trend,
)
from finance_mcp.dx import (
    dx_counterfactual,
    dx_evidence_rows,
    dx_ingest,
    dx_memo,
    dx_report,
    dx_segment_stats,
    dx_time_stability,
)
from finance_mcp.cim import cim_analyze
from finance_mcp.eu_ai_act import ai_act_audit
from finance_mcp.eval import eval_pe_output
from finance_mcp.explainer import explain_decision
from finance_mcp.normalize import normalize_portco
from finance_mcp.seller_pack import exit_proof_pack

mcp = FastMCP("Private-Equity MCP Server")

# Decision-Optimization Diagnostic (DX) — Claude-native, pandas-only
mcp.add_tool(dx_ingest)
mcp.add_tool(dx_segment_stats)
mcp.add_tool(dx_time_stability)
mcp.add_tool(dx_counterfactual)
mcp.add_tool(dx_evidence_rows)
mcp.add_tool(dx_memo)
mcp.add_tool(dx_report)

# Model-to-Narrative — turns OpportunityMaps into board memos
mcp.add_tool(explain_decision)

# CIM Red-Flag Extractor — runs heuristic diligence flags over a SEC filing
mcp.add_tool(cim_analyze)

# LLM Eval for PE — scores any PE doc AI output against its source ground truth
mcp.add_tool(eval_pe_output)

# Seller-Side Diligence Pack — exit-prep AI EBITDA proof pack with sensitivity
mcp.add_tool(exit_proof_pack)

# EU AI Act Compliance — Article 6 classification + per-Article doc skeleton
mcp.add_tool(ai_act_audit)

# Portfolio Normalization — N portco CSVs in different formats → unified schema
mcp.add_tool(normalize_portco)

# Benchmarking (BX) — cross-portco + within-portco time-series
mcp.add_tool(bx_ingest_corpus)
mcp.add_tool(bx_portco_rank)
mcp.add_tool(bx_archetype_index)
mcp.add_tool(bx_peer_group)
mcp.add_tool(bx_report)
mcp.add_tool(bx_snapshot)
mcp.add_tool(bx_trend)
mcp.add_tool(bx_delta)


@mcp.tool
def ping() -> str:
    """Health check — confirms the PE MCP server is running and reachable by Claude Code."""
    return "Private-Equity MCP Server is running. Ready to execute PE analysis."


@mcp.tool
def validate_environment() -> dict:
    """
    Detect Python environment and validate required PE-analysis packages are installed.

    Returns a dict mapping package name → version string (or 'MISSING' if not installed).
    Claude Code should call this tool first to confirm the environment is ready before
    generating analysis code.
    """
    packages = {
        "pandas": "pandas",
        "numpy": "numpy",
        "matplotlib": "matplotlib",
        "scipy": "scipy",
    }
    results: dict[str, str] = {}
    missing: list[str] = []
    for display_name, import_name in packages.items():
        try:
            mod = importlib.import_module(import_name)
            results[display_name] = getattr(mod, "__version__", "installed")
        except ImportError:
            results[display_name] = "MISSING"
            missing.append(display_name)

    if missing:
        results["_status"] = "INCOMPLETE"
        results["_install_hint"] = (
            f"Missing packages: {', '.join(missing)}. "
            f"Run: pip install {' '.join(missing)}"
        )
    else:
        results["_status"] = "OK"

    print(f"[pe-mcp] Environment check: {results['_status']}", file=sys.stderr)
    return results


if __name__ == "__main__":
    print("[pe-mcp] Starting Private-Equity MCP Server (stdio)", file=sys.stderr)
    mcp.run()
