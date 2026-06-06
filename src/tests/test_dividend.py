"""dividend.py の純関数テスト。div_map / months_map をスタブ注入し通信・認証情報なしで実行。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dividend as dv  # noqa: E402
import portfolio as pf  # noqa: E402


ROWS = [
    # jp 高配当: 100株 取得200 現在250
    {"ticker": "1489", "name": "日経高配当50", "asset_class": "jp_dividend",
     "shares": 100, "cost_per_share": 200, "sector": "分散ETF", "market": "jp"},
    # us 高配当: 10株 取得25 現在30
    {"ticker": "SCHD", "name": "SCHD", "asset_class": "us_dividend",
     "shares": 10, "cost_per_share": 25, "sector": "分散ETF", "market": "us"},
    # jp REIT: 5株 取得2000 現在1800・別セクター
    {"ticker": "1343", "name": "東証REIT", "asset_class": "reit",
     "shares": 5, "cost_per_share": 2000, "sector": "REIT", "market": "jp"},
]
PRICES = {"1489": 250, "SCHD": 30, "1343": 1800}
DIV = {"1489": 5.0, "SCHD": 1.0, "1343": 80.0}  # 1株あたり年間配当
MONTHS = {"1489": [1, 7], "SCHD": [3, 6, 9, 12], "1343": []}  # 1343は月不明


@pytest.fixture
def holdings():
    return pf.build_holdings(ROWS, PRICES)


def test_market_resolved_from_csv(holdings):
    assert {h.ticker: h.market for h in holdings} == {"1489": "jp", "SCHD": "us", "1343": "jp"}


def test_annual_dividend_per_holding(holdings):
    # 1489: 5 × 100 = 500
    h = holdings[0]
    assert dv.annual_dividend(h, DIV) == 500


def test_after_tax_rates():
    assert dv.after_tax(1000, "jp") == pytest.approx(1000 * (1 - 0.20315))
    assert dv.after_tax(1000, "us") == pytest.approx(1000 * (1 - 0.282835))
    # 未知marketはjp税率
    assert dv.after_tax(1000, "xx") == pytest.approx(1000 * (1 - 0.20315))


def test_total_dividend_pre_and_post_tax(holdings):
    # gross: 500 + 10 + 400 = 910
    assert dv.total_annual_dividend(holdings, DIV, pre_tax=True) == 910
    expected_net = (500 * (1 - 0.20315)) + (10 * (1 - 0.282835)) + (400 * (1 - 0.20315))
    assert dv.total_annual_dividend(holdings, DIV, pre_tax=False) == pytest.approx(expected_net)


def test_yields(holdings):
    # cost: 20000 + 250 + 10000 = 30250 / market: 25000 + 300 + 9000 = 34300 / div: 910
    assert dv.yield_on_cost(holdings, DIV) == pytest.approx(910 / 30250 * 100)
    assert dv.yield_on_market(holdings, DIV) == pytest.approx(910 / 34300 * 100)


def test_dividend_by_month_distributes_evenly(holdings):
    by_month = dv.dividend_by_month(holdings, DIV, MONTHS, pre_tax=True)
    # 1489=500を1月・7月へ250ずつ / SCHD=10を3,6,9,12へ2.5ずつ / 1343=400は月不明
    assert by_month[1] == pytest.approx(250)
    assert by_month[7] == pytest.approx(250)
    assert by_month[3] == pytest.approx(2.5)
    assert by_month[dv.UNKNOWN_MONTH] == pytest.approx(400)
    # 12ヶ月＋不明の合計＝総配当
    assert sum(v for v in by_month.values()) == pytest.approx(910)


def test_dividend_by_sector(holdings):
    by_sector = dv.dividend_by_sector(holdings, DIV, pre_tax=True)
    assert by_sector["分散ETF"] == pytest.approx(510)  # 1489 500 + SCHD 10
    assert by_sector["REIT"] == pytest.approx(400)


def test_dividend_by_market(holdings):
    by_market = dv.dividend_by_market(holdings, DIV, pre_tax=True)
    assert by_market["jp"] == pytest.approx(900)  # 1489 500 + 1343 400
    assert by_market["us"] == pytest.approx(10)


def test_empty_and_missing_div_safe():
    assert dv.total_annual_dividend([], {}, pre_tax=True) == 0
    assert dv.yield_on_cost([], {}) == 0.0
    # div_map に無い銘柄は配当0
    h = pf.build_holdings(
        [{"ticker": "X", "name": "x", "asset_class": "index",
          "shares": 10, "cost_per_share": 100, "market": "us"}],
        {},
    )
    assert dv.total_annual_dividend(h, {}) == 0
