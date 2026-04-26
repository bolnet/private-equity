"""
Eval module — score any PE document AI output (CIM extractor, DDQ generator,
IC memo drafter, board memo) on citation accuracy, hallucination rate,
coverage, and consistency.
"""
from finance_mcp.eval.eval import eval_pe_output

__all__ = ["eval_pe_output"]
