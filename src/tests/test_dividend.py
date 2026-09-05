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


# --- 口座区分による非課税（NISA）---


def _holding(market: str, account: str, shares: float = 100.0) -> pf.Holding:
    return pf.Holding(
        ticker="T", name="テスト", asset_class="jp_dividend", shares=shares,
        cost_per_share=100.0, price=100.0, market=market, account=account,
    )


def test_nisa_domestic_dividend_is_untaxed():
    assert dv.after_tax(1000.0, "jp", "nisa_growth") == 1000.0
    assert dv.after_tax(1000.0, "jp", "nisa_tsumitate") == 1000.0
    assert dv.after_tax(1000.0, "jp", "nisa_old") == 1000.0


def test_nisa_us_dividend_still_pays_10_percent_withholding():
    """NISA でも米国株の配当は現地で10%源泉徴収される。

    「特定以外は非課税」を額面どおり0%にすると手取りを過大表示する。NISA では
    外国税額控除も使えないため、この10%は取り戻せない。
    """
    assert dv.after_tax(1000.0, "us", "nisa_growth") == pytest.approx(900.0)
    # 特定口座なら米10%＋国内20.315%の合算
    assert dv.after_tax(1000.0, "us", "specific") == pytest.approx(1000.0 * (1 - 0.282835))


def test_specific_and_blank_account_are_taxed():
    # 空欄は特定口座扱い（未設定のデータを非課税と誤判定しない＝安全側）
    assert dv.after_tax(1000.0, "jp", "specific") == pytest.approx(1000.0 * (1 - 0.20315))
    assert dv.after_tax(1000.0, "jp", "") == pytest.approx(1000.0 * (1 - 0.20315))
    assert dv.after_tax(1000.0, "jp") == pytest.approx(1000.0 * (1 - 0.20315))
    assert dv.is_taxable("") and dv.is_taxable("specific")
    assert not dv.is_taxable("nisa_old")


def test_unknown_account_is_treated_as_taxable():
    # 表記ゆれや手入力ミスで未知の値が入っても非課税にはしない
    assert dv.is_taxable("なにか") is False or dv.after_tax(100.0, "jp", "なにか") < 100.0


def test_holding_dividend_uses_account_of_each_holding():
    div = {"T": 10.0}
    taxed = dv.holding_dividend(_holding("jp", "specific"), div, pre_tax=False)
    untaxed = dv.holding_dividend(_holding("jp", "nisa_growth"), div, pre_tax=False)
    assert untaxed > taxed
    assert untaxed == pytest.approx(1000.0)


def test_total_dividend_mixes_taxable_and_untaxed_accounts():
    """集計は holding_dividend を通るので、口座別の税率が自動的に効く。"""
    div = {"T": 10.0}
    mixed = [_holding("jp", "specific"), _holding("jp", "nisa_growth")]
    total = dv.total_annual_dividend(mixed, div, pre_tax=False)
    assert total == pytest.approx(1000.0 * (1 - 0.20315) + 1000.0)


# --- 実効税率（シミュレーションへ渡す）---


def test_effective_tax_rate_reflects_account_mix():
    div = {"T": 10.0}
    all_taxed = [_holding("jp", "specific")]
    half = [_holding("jp", "specific"), _holding("jp", "nisa_growth")]
    all_nisa = [_holding("jp", "nisa_growth")]

    assert dv.effective_tax_rate(all_taxed, div) == pytest.approx(0.20315)
    assert dv.effective_tax_rate(half, div) == pytest.approx(0.20315 / 2)
    assert dv.effective_tax_rate(all_nisa, div) == 0.0


def test_effective_tax_rate_without_dividend_falls_back_to_domestic_rate():
    # 配当0だと 0/0 になる。将来の見積もりに使うため国内税率を返す
    assert dv.effective_tax_rate([_holding("jp", "nisa_growth")], {}) == pytest.approx(0.20315)
    assert dv.effective_tax_rate([], {}) == pytest.approx(0.20315)
