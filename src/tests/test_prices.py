"""prices.py の純関数テスト。yfinance は呼ばず FastInfo はスタブで注入する。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prices as pr  # noqa: E402


class _CamelKeyInfo:
    """yfinance 1.7 系：辞書キーは camelCase、属性は snake_case。"""

    def __init__(self, data: dict):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __getattr__(self, name):
        camel = pr._to_camel(name)
        if camel in self._data:
            return self._data[camel]
        raise AttributeError(name)


class _SnakeKeyInfo:
    """旧 yfinance：辞書キーが snake_case。"""

    def __init__(self, data: dict):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)


class _AttrOnlyInfo:
    """辞書アクセスを持たず属性だけのオブジェクト。"""

    last_price = 2995.0


def test_fast_info_value_reads_camel_case_keys():
    # 2026-09-02 の実害：yfinance 1.7.0 で lastPrice に変わり .get("last_price") が
    # 例外も出さず None を返して時価取得が全滅した。この形で必ず取れること。
    info = _CamelKeyInfo({"lastPrice": 2995.0, "yearHigh": 30800.0})
    assert pr.fast_info_value(info, "last_price") == 2995.0
    assert pr.fast_info_value(info, "year_high") == 30800.0


def test_fast_info_value_reads_snake_case_keys():
    info = _SnakeKeyInfo({"last_price": 100.0})
    assert pr.fast_info_value(info, "last_price") == 100.0


def test_fast_info_value_reads_attribute_only_object():
    assert pr.fast_info_value(_AttrOnlyInfo(), "last_price") == 2995.0


def test_fast_info_value_returns_none_when_missing_or_zero():
    assert pr.fast_info_value(_CamelKeyInfo({}), "last_price") is None
    # 0 は「値なし」と同じ扱い（価格0は不正データ）
    assert pr.fast_info_value(_CamelKeyInfo({"lastPrice": 0}), "last_price") is None
    assert pr.fast_info_value(object(), "last_price") is None


def test_to_camel_conversion():
    assert pr._to_camel("last_price") == "lastPrice"
    assert pr._to_camel("year_high") == "yearHigh"
    assert pr._to_camel("open") == "open"


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
