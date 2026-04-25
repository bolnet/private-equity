"""Shared pytest fixtures for finance_mcp test suite."""
import os
import sys
import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock

# Ensure the in-repo src/ wins over any stale editable-install path
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


@pytest.fixture
def sample_price_df():
    """A minimal valid price DataFrame matching yfinance 0.2.x output (auto_adjust=True)."""
    dates = pd.date_range("2023-01-01", periods=10, freq="B")
    return pd.DataFrame(
        {
            "Open": np.random.uniform(150, 160, 10),
            "High": np.random.uniform(160, 170, 10),
            "Low": np.random.uniform(140, 150, 10),
            "Close": np.random.uniform(150, 165, 10),  # adjusted close
            "Volume": np.random.randint(1_000_000, 5_000_000, 10),
        },
        index=dates,
    )


@pytest.fixture
def empty_df():
    """Empty DataFrame — simulates no data returned from yfinance."""
    return pd.DataFrame()


@pytest.fixture
def tmp_output_dir(tmp_path):
    """Temporary directory that mimics finance_output/charts/."""
    charts = tmp_path / "finance_output" / "charts"
    charts.mkdir(parents=True)
    return tmp_path
