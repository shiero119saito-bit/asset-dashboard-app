"""portfolio.py の純関数テスト。時価はスタブ（price_map）で注入し、認証情報・通信なしで実行可能。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import portfolio as pf  # noqa: E402


ROWS = [
    {"ticker": "2559", "name": "オルカン", "asset_class": "index", "shares": 100, "cost_per_share": 200},
    {"ticker": "SCHD", "name": "SCHD", "asset_class": "us_dividend", "shares": 10, "cost_per_share": 25},
    {"ticker": "1343", "name": "東証REIT", "asset_class": "reit", "shares": 5, "cost_per_share": 2000},
]
PRICES = {"2559": 250, "SCHD": 30, "1343": 1800}


@pytest.fixture
def holdings():
    return pf.build_holdings(ROWS, PRICES)


def test_holding_values(holdings):
    h = holdings[0]  # 2559: 100株 取得200 現在250
    assert h.cost_value == 20000
    assert h.market_value == 25000
    assert h.gain == 5000
    assert h.gain_rate == pytest.approx(25.0)


def test_missing_price_falls_back_to_cost():
    # 時価欠損 → 取得単価で代替し評価額=取得額（含み損益0）
    h = pf.build_holdings(
        [{"ticker": "X", "name": "x", "asset_class": "index", "shares": 10, "cost_per_share": 100}],
        {},
    )[0]
    assert h.price == 100
    assert h.market_value == 1000
    assert h.gain == 0


def test_unknown_asset_class_raises():
    with pytest.raises(ValueError):
        pf.build_holdings(
            [{"ticker": "X", "name": "x", "asset_class": "crypto", "shares": 1, "cost_per_share": 1}],
            {},
        )


def test_totals(holdings):
    # cost: 20000 + 250 + 10000 = 30250 / market: 25000 + 300 + 9000 = 34300
    assert pf.total_cost(holdings) == 30250
    assert pf.total_market(holdings) == 34300
    assert pf.total_gain(holdings) == 4050
    assert pf.total_gain_rate(holdings) == pytest.approx(4050 / 30250 * 100)


def test_allocation_sums_to_100(holdings):
    alloc = pf.allocation_by_class(holdings)
    assert set(alloc.keys()) == set(pf.ASSET_CLASSES)
    assert sum(alloc.values()) == pytest.approx(100.0)
    # jp_dividend は保有なし → 0%
    assert alloc["jp_dividend"] == 0.0


def test_allocation_drift_sign(holdings):
    # index 現在 25000/34300≒72.9% > 目標60 → 正のドリフト
    drift = pf.allocation_drift(holdings)
    assert drift["index"] > 0
    # jp_dividend 0% < 目標15 → 負
    assert drift["jp_dividend"] == pytest.approx(-15.0)


def test_empty_portfolio_is_safe():
    empty = []
    assert pf.total_market(empty) == 0
    assert pf.total_gain_rate(empty) == 0.0
    assert pf.allocation_by_class(empty) == {ac: 0.0 for ac in pf.ASSET_CLASSES}
