"""dataio.py の純関数テスト（I/O なし）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dataio  # noqa: E402
import portfolio as pf  # noqa: E402


FULL_CSV = (
    "ticker,name,asset_class,shares,cost_per_share,sector,market,div_per_share\n"
    "1489,日経高配当50,jp_dividend,30,55000,分散ETF,jp,2200\n"
    "SCHD,Schwab US Dividend,us_dividend,60,2600,分散ETF,us,110\n"
)

MINIMAL_CSV = (
    "ticker,name,asset_class,shares,cost_per_share\n"
    "2559,オルカン,index,100,15000\n"
)


def test_parse_full_csv():
    rows = dataio.parse_holdings_csv(FULL_CSV)
    assert len(rows) == 2
    assert rows[0]["ticker"] == 1489 or str(rows[0]["ticker"]) == "1489"
    assert rows[1]["sector"] == "分散ETF"
    # 追加列を含めて build_holdings まで通ること
    holdings = pf.build_holdings(rows, {})
    assert {h.market for h in holdings} == {"jp", "us"}


def test_parse_minimal_csv_optional_columns_absent():
    # 追加列が無くても必須列が揃えば parse でき、build_holdings がデフォルト補完
    rows = dataio.parse_holdings_csv(MINIMAL_CSV)
    holdings = pf.build_holdings(rows, {})
    assert holdings[0].sector == "その他"
    assert holdings[0].market == "jp"  # 4桁数字→jp 推定


def test_missing_required_column_raises():
    bad = "ticker,name,asset_class,shares\n2559,オルカン,index,100\n"  # cost_per_share 欠落
    with pytest.raises(ValueError):
        dataio.parse_holdings_csv(bad)


def test_empty_input_returns_empty():
    assert dataio.parse_holdings_csv("") == []
    assert dataio.parse_holdings_csv("   ") == []
    assert dataio.parse_holdings_csv(None) == []
