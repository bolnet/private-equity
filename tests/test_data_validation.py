"""
Tests for data validation wrapper.
Requirements: INFRA-06

Wave 0: stubs only. Implementations land in plan 01-02 (validators.py).
Full test commands:
  python3 -m pytest tests/test_data_validation.py -v
"""
import sys
import os
import pytest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.mark.xfail(reason="validators.py not yet implemented — lands in plan 01-02", strict=False)
def test_validate_dataframe_raises_on_empty():
    """INFRA-06: validate_dataframe raises user-friendly error on empty DataFrame."""
    from finance_mcp.validators import validate_dataframe, ValidationError
    with pytest.raises(ValidationError, match="No data"):
        validate_dataframe(pd.DataFrame(), ticker="TEST")


@pytest.mark.xfail(reason="validators.py not yet implemented — lands in plan 01-02", strict=False)
def test_validate_dataframe_raises_on_missing_close():
    """INFRA-06: validate_dataframe raises when Close column is absent."""
    from finance_mcp.validators import validate_dataframe, ValidationError
    df = pd.DataFrame({"Open": [1.0], "High": [2.0]})
    with pytest.raises(ValidationError, match="Close"):
        validate_dataframe(df, ticker="TEST")


@pytest.mark.xfail(reason="validators.py not yet implemented — lands in plan 01-02", strict=False)
def test_validate_dataframe_passes_valid_df(sample_price_df):
    """INFRA-06: validate_dataframe returns None (no error) for a valid DataFrame."""
    from finance_mcp.validators import validate_dataframe
    result = validate_dataframe(sample_price_df, ticker="AAPL")
    assert result is None
