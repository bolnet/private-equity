"""
Read ``src/finance_mcp/server.py`` and enumerate every registered MCP tool.

We treat each ``mcp.add_tool(<name>)`` call and each ``@mcp.tool`` decorated
function as one *agent* in the inventory. The audit tool then attaches a
modeled token budget + synthetic-but-defensible last-call timestamp to each.

This module is read-only on ``server.py``. It uses the ``ast`` standard
library so we never execute user code while enumerating, and it returns
immutable dataclass rows so downstream stages can't mutate the inventory.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from fastmcp.exceptions import ToolError


@dataclass(frozen=True)
class RegisteredTool:
    """One MCP tool registered on the server, located by AST."""

    name: str
    line: int
    decorator: bool  # True if registered via @mcp.tool, False for add_tool()


def enumerate_registered_tools(server_path: Path) -> tuple[RegisteredTool, ...]:
    """Parse ``server.py`` and return every registered MCP tool.

    Args:
        server_path: Path to the FastMCP server module (e.g.
            ``src/finance_mcp/server.py``).

    Returns:
        A tuple of ``RegisteredTool`` rows in source order.

    Raises:
        ToolError if the file is missing, unreadable, or parses to no tools.
    """
    if not server_path.exists():
        raise ToolError(f"server module not found: {server_path}")

    try:
        source = server_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"could not read server module: {exc}") from exc

    try:
        tree = ast.parse(source, filename=str(server_path))
    except SyntaxError as exc:
        raise ToolError(f"server module has invalid Python syntax: {exc}") from exc

    add_tool_rows = _find_add_tool_calls(tree)
    decorator_rows = _find_mcp_tool_decorators(tree)

    rows = tuple(
        sorted(
            (*add_tool_rows, *decorator_rows),
            key=lambda r: r.line,
        )
    )

    if not rows:
        raise ToolError(
            f"no MCP tool registrations found in {server_path} — "
            "expected mcp.add_tool(...) or @mcp.tool"
        )

    return rows


def _find_add_tool_calls(tree: ast.AST) -> tuple[RegisteredTool, ...]:
    """Locate every ``mcp.add_tool(<name>)`` call (positional Name arg)."""
    out: list[RegisteredTool] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != "add_tool":
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "mcp"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Name):
            # Skip non-Name args (e.g. lambda) — they're not tool refs.
            continue
        out.append(
            RegisteredTool(
                name=first.id,
                line=node.lineno,
                decorator=False,
            )
        )
    return tuple(out)


def _find_mcp_tool_decorators(tree: ast.AST) -> tuple[RegisteredTool, ...]:
    """Locate every ``@mcp.tool`` (or ``@mcp.tool(...)``) decorated function."""
    out: list[RegisteredTool] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if _is_mcp_tool_decorator(deco):
                out.append(
                    RegisteredTool(
                        name=node.name,
                        line=node.lineno,
                        decorator=True,
                    )
                )
                break
    return tuple(out)


def _is_mcp_tool_decorator(deco: ast.expr) -> bool:
    """Recognise ``@mcp.tool`` and ``@mcp.tool(...)``."""
    target = deco.func if isinstance(deco, ast.Call) else deco
    return (
        isinstance(target, ast.Attribute)
        and target.attr == "tool"
        and isinstance(target.value, ast.Name)
        and target.value.id == "mcp"
    )
