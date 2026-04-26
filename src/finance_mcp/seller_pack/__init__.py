"""
Seller-side exit-prep module — generates a defensible AI EBITDA proof pack
that a portco can hand to its M&A advisor before banker engagement, so the
buyer's AI diligence team finds nothing surprising.

The seller-side twin to AlixPartners' AI Disruption Score: every $ of
claimed AI uplift traces to a structured DX/BX artifact with row-level
citations, methodology disclosure, sensitivity ranges, and a defensibility
checklist.
"""
from finance_mcp.seller_pack.pack import exit_proof_pack

__all__ = ["exit_proof_pack"]
