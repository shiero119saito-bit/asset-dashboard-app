"""prices.py の純関数テスト（convert_us_values_to_jpy）。yfinance呼び出しは含まない。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prices as pr  # noqa: E402


def test_convert_us_values_to_jpy_converts_only_us_tickers():
    value_map = {"VYM": 65.0, "1605": 4051.0}
    result = pr.convert_us_values_to_jpy(value_map, {"VYM"}, fx_rate=150.0)
    assert result == {"VYM": 9750.0, "1605": 4051.0}


def test_convert_us_values_to_jpy_drops_us_tickers_when_fx_rate_none():
    value_map = {"VYM": 65.0, "1605": 4051.0}
    result = pr.convert_us_values_to_jpy(value_map, {"VYM"}, fx_rate=None)
    assert result == {"1605": 4051.0}


def test_convert_us_values_to_jpy_no_us_tickers_returns_unchanged():
    value_map = {"1605": 4051.0}
    result = pr.convert_us_values_to_jpy(value_map, set(), fx_rate=150.0)
    assert result == value_map


def test_convert_us_values_to_jpy_empty_value_map():
    assert pr.convert_us_values_to_jpy({}, {"VYM"}, fx_rate=150.0) == {}
