"""
Output conventions for all finance_mcp workflows.

This module is imported by every analysis workflow. It enforces:
  1. Headless matplotlib backend (Agg) — no GUI, no show() calls
  2. Consistent output directory structure (finance_output/charts/)
  3. Mandatory investment disclaimer on all outputs
  4. Plain-English-first output ordering

IMPORTANT: import matplotlib and call matplotlib.use("Agg") BEFORE importing pyplot.
This module handles that — do not import matplotlib.pyplot before importing this module.
"""
import os
import matplotlib
matplotlib.use("Agg")  # Must be set before any pyplot import — GUI display is disabled
import matplotlib.pyplot as plt

DISCLAIMER = (
    "For educational/informational purposes only. "
    "Not financial advice. "
    "Past results do not guarantee future performance."
)

CHART_DIR = os.path.join("finance_output", "charts")
SCRIPT_DIR = "finance_output"


def ensure_output_dirs() -> None:
    """Create finance_output/ and finance_output/charts/ directories if they do not exist."""
    os.makedirs(CHART_DIR, exist_ok=True)
    os.makedirs(SCRIPT_DIR, exist_ok=True)


def save_chart(fig: plt.Figure, filename: str) -> str:
    """
    Save a matplotlib figure as a PNG file to finance_output/charts/.

    Always call this instead of calling savefig() or show() directly.
    Closes the figure after saving to free memory.

    Args:
        fig: matplotlib Figure object to save.
        filename: Filename (basename only, e.g. "aapl_price.png"). .png extension required.

    Returns:
        Absolute path to the saved PNG file.
    """
    ensure_output_dirs()
    path = os.path.abspath(os.path.join(CHART_DIR, filename))
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def format_output(
    plain_english: str,
    data_section: str = "",
    chart_paths: list | None = None,
) -> str:
    """
    Format a complete finance output in the mandatory order:
      1. Plain-English interpretation (ALWAYS first)
      2. Data section (tables, metrics) — optional
      3. Chart file paths — optional
      4. Disclaimer (ALWAYS last)

    Args:
        plain_english: Human-readable summary of the analysis result. Required.
        data_section: Optional DataFrame printout, metric table, or raw numbers.
        chart_paths: Optional list of absolute PNG file paths from save_chart().

    Returns:
        Formatted string ready for display to the user.
    """
    parts = [plain_english.strip()]
    if data_section:
        parts.append(data_section.strip())
    if chart_paths:
        chart_lines = ["Charts saved:"] + [f"  {p}" for p in chart_paths]
        parts.append("\n".join(chart_lines))
    parts.append(DISCLAIMER)
    return "\n\n".join(parts)
