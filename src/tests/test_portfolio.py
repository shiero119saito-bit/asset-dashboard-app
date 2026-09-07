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


def test_price_falls_back_to_saved_price_when_live_missing():
    # クラウドは Yahoo が401でライブ取得できない → CSVに保存した時価を使う
    rows = [{"ticker": "X", "name": "x", "asset_class": "index", "shares": 10,
             "cost_per_share": 100, "price": "150", "price_asof": "2026-09-03"}]
    h = pf.build_holdings(rows, {})[0]
    assert h.price == 150.0
    assert h.market_value == 1500.0
    assert h.price_asof == "2026-09-03"  # いつ時点かをUIが出せる


def test_live_price_wins_over_saved_price():
    rows = [{"ticker": "X", "name": "x", "asset_class": "index", "shares": 10,
             "cost_per_share": 100, "price": "150", "price_asof": "2026-09-03"}]
    h = pf.build_holdings(rows, {"X": 200.0})[0]
    assert h.price == 200.0
    assert h.price_asof == ""  # ライブ値は「現在値」なので日付を出さない


def test_saved_price_invalid_or_zero_falls_back_to_cost():
    for bad in ("", "0", "nan", "abc", "-5"):
        rows = [{"ticker": "X", "name": "x", "asset_class": "index", "shares": 10,
                 "cost_per_share": 100, "price": bad}]
        assert pf.build_holdings(rows, {})[0].price == 100.0, f"price={bad!r}"


def test_price_column_absent_is_safe():
    # price 列を持たない旧形式のCSVでも従来どおり取得単価にフォールバックする
    rows = [{"ticker": "X", "name": "x", "asset_class": "index", "shares": 10, "cost_per_share": 100}]
    assert pf.build_holdings(rows, {})[0].price == 100.0


def test_allocation_by_sector_percentages():
    rows = [
        {"ticker": "A", "name": "a", "asset_class": "index", "shares": 10,
         "cost_per_share": 100, "sector": "分散ETF"},
        {"ticker": "B", "name": "b", "asset_class": "reit", "shares": 10,
         "cost_per_share": 100, "sector": "REIT"},
    ]
    holdings = pf.build_holdings(rows, {"A": 100, "B": 300})  # 評価額 1000 / 3000
    alloc = pf.allocation_by_sector(holdings)
    assert alloc == pytest.approx({"分散ETF": 25.0, "REIT": 75.0})


def test_allocation_by_market_region_percentages():
    rows = [
        {"ticker": "1001", "name": "jp", "asset_class": "jp_dividend", "shares": 10,
         "cost_per_share": 100, "market": "jp"},
        {"ticker": "SCHD", "name": "us", "asset_class": "us_dividend", "shares": 10,
         "cost_per_share": 100, "market": "us"},
    ]
    holdings = pf.build_holdings(rows, {"1001": 100, "SCHD": 100})  # 評価額 均等
    alloc = pf.allocation_by_market_region(holdings)
    assert alloc == pytest.approx({"jp": 50.0, "us": 50.0})


def test_allocation_by_sector_empty_portfolio_is_safe():
    assert pf.allocation_by_sector([]) == {}
    assert pf.allocation_by_market_region([]) == {}


def test_jp_dividend_by_purpose_groups_and_ignores_other_classes():
    rows = [
        {"ticker": "1001", "name": "配当株", "asset_class": "jp_dividend", "shares": 10,
         "cost_per_share": 100, "purpose": "dividend"},
        {"ticker": "1002", "name": "優待株", "asset_class": "jp_dividend", "shares": 5,
         "cost_per_share": 200, "purpose": "yutai"},
        {"ticker": "1003", "name": "未分類株", "asset_class": "jp_dividend", "shares": 1,
         "cost_per_share": 300},
        {"ticker": "2559", "name": "オルカン", "asset_class": "index", "shares": 100,
         "cost_per_share": 200, "purpose": "dividend"},
    ]
    holdings = pf.build_holdings(rows, {})
    groups = pf.jp_dividend_by_purpose(holdings)
    assert [h.ticker for h in groups["dividend"]] == ["1001"]
    assert [h.ticker for h in groups["yutai"]] == ["1002"]
    assert [h.ticker for h in groups[""]] == ["1003"]
    # index クラスは jp_dividend でないため除外される
    assert all(h.ticker != "2559" for group in groups.values() for h in group)


# --- 業種（東証33業種）---


INDUSTRY_ROWS = [
    {"ticker": "9432", "name": "NTT", "asset_class": "jp_dividend", "shares": 10,
     "cost_per_share": 100, "industry": "情報・通信業"},
    {"ticker": "9433", "name": "KDDI", "asset_class": "jp_dividend", "shares": 10,
     "cost_per_share": 100, "industry": "情報・通信業"},
    {"ticker": "8267", "name": "イオン", "asset_class": "jp_dividend", "shares": 10,
     "cost_per_share": 100, "industry": "小売業"},
    {"ticker": "9999", "name": "未設定株", "asset_class": "jp_dividend", "shares": 10,
     "cost_per_share": 100},  # industry 欠損
    {"ticker": "2559", "name": "オルカン", "asset_class": "index", "shares": 10,
     "cost_per_share": 100, "industry": "ETF・投信"},
]
INDUSTRY_PRICES = {"9432": 100, "9433": 100, "8267": 100, "9999": 100, "2559": 600}


def test_allocation_by_industry_percentages():
    """全資産（ETF含む）で合計100%になる。同一業種は合算される。"""
    holdings = pf.build_holdings(INDUSTRY_ROWS, INDUSTRY_PRICES)
    alloc = pf.allocation_by_industry(holdings)  # 評価額 1000*4 + 6000 = 10000
    assert alloc == pytest.approx({
        "情報・通信業": 20.0, "小売業": 10.0, "未分類": 10.0, "ETF・投信": 60.0,
    })
    assert sum(alloc.values()) == pytest.approx(100.0)


def test_allocation_by_industry_blank_goes_to_unclassified():
    holdings = pf.build_holdings(INDUSTRY_ROWS, INDUSTRY_PRICES)
    assert pf.INDUSTRY_UNCLASSIFIED in pf.allocation_by_industry(holdings)


def test_allocation_by_industry_empty_portfolio_is_safe():
    assert pf.allocation_by_industry([]) == {}


def test_jp_stocks_only_excludes_funds_and_keeps_source():
    holdings = pf.build_holdings(INDUSTRY_ROWS, INDUSTRY_PRICES)
    jp = pf.jp_stocks_only(holdings)
    assert [h.ticker for h in jp] == ["9432", "9433", "8267", "9999"]
    # 絞り込み後も構成比は100%になる（母数が絞った側で再計算される）
    assert sum(pf.allocation_by_industry(jp).values()) == pytest.approx(100.0)
    assert len(holdings) == 5  # 元リストは変更しない


def test_industries_constant_covers_data_values():
    """CSVに入る値は INDUSTRIES に含まれる＝エディタの選択肢と集計がずれない。"""
    holdings = pf.build_holdings(INDUSTRY_ROWS, INDUSTRY_PRICES)
    for h in holdings:
        assert h.industry == "" or h.industry in pf.INDUSTRIES


# --- 用途フィルタ（配当画面の表示絞り込み）---


PURPOSE_ROWS = [
    {"ticker": "1001", "name": "配当株", "asset_class": "jp_dividend", "shares": 10,
     "cost_per_share": 100, "purpose": "dividend"},
    {"ticker": "1002", "name": "優待株", "asset_class": "jp_dividend", "shares": 5,
     "cost_per_share": 200, "purpose": " Yutai "},  # 前後空白・大文字も同一視する
    {"ticker": "1003", "name": "未分類株", "asset_class": "jp_dividend", "shares": 1,
     "cost_per_share": 300},
    {"ticker": "2559", "name": "オルカン", "asset_class": "index", "shares": 100,
     "cost_per_share": 200, "purpose": "growth"},
]


def test_filter_by_purpose_selects_only_matching():
    holdings = pf.build_holdings(PURPOSE_ROWS, {})
    got = pf.filter_by_purpose(holdings, ("dividend",))
    assert [h.ticker for h in got] == ["1001"]
    # asset_class は無視＝インデックスでも purpose が一致すれば残る
    got = pf.filter_by_purpose(holdings, ("growth",))
    assert [h.ticker for h in got] == ["2559"]


def test_filter_by_purpose_accepts_multiple_and_normalizes():
    holdings = pf.build_holdings(PURPOSE_ROWS, {})
    got = pf.filter_by_purpose(holdings, ("dividend", "yutai"))
    assert [h.ticker for h in got] == ["1001", "1002"]


def test_filter_by_purpose_empty_selection_returns_all():
    """絞り込みなし（全資産）は全件。元リストは変更しない。"""
    holdings = pf.build_holdings(PURPOSE_ROWS, {})
    got = pf.filter_by_purpose(holdings, ())
    assert [h.ticker for h in got] == [h.ticker for h in holdings]
    assert got is not holdings


def test_filter_by_purpose_unknown_purpose_returns_empty():
    holdings = pf.build_holdings(PURPOSE_ROWS, {})
    assert pf.filter_by_purpose(holdings, ("nothing",)) == []


# --- 口座区分（表示用の合算）---


def _acc_holding(ticker: str, account: str, shares: float, cost: float) -> pf.Holding:
    return pf.Holding(
        ticker=ticker, name=ticker, asset_class="jp_dividend", shares=shares,
        cost_per_share=cost, price=cost, market="jp", account=account,
    )


def test_group_by_ticker_merges_accounts_for_display():
    """計算は口座別、表示は銘柄別。同じ銘柄が何行にも分かれて見えないようにする。"""
    holdings = [
        _acc_holding("1605", "specific", 10, 100),
        _acc_holding("1605", "nisa_growth", 5, 200),
        _acc_holding("2003", "specific", 1, 300),
    ]
    groups = pf.group_by_ticker(holdings)
    assert list(groups) == ["1605", "2003"]  # 出現順を保つ
    assert len(groups["1605"]) == 2
    assert sum(h.shares for h in groups["1605"]) == 15


def test_merged_cost_per_share_is_weighted_by_shares():
    """単純平均だと少数株の口座が過大に効く。取得額合計 ÷ 株数合計で出す。"""
    group = [_acc_holding("1605", "specific", 10, 100), _acc_holding("1605", "nisa_growth", 90, 200)]
    # 単純平均なら150。加重平均は (10*100 + 90*200) / 100 = 190
    assert pf.merged_cost_per_share(group) == 190.0


def test_merged_cost_per_share_zero_shares():
    assert pf.merged_cost_per_share([]) == 0.0
    assert pf.merged_cost_per_share([_acc_holding("X", "specific", 0, 100)]) == 0.0


def test_allocation_by_account_treats_blank_as_specific():
    # 空欄は特定口座に集計する（税計算の扱いと揃える）
    holdings = [
        _acc_holding("A", "", 1, 100),
        _acc_holding("B", "specific", 1, 100),
        _acc_holding("C", "nisa_growth", 2, 100),
    ]
    alloc = pf.allocation_by_account(holdings)
    assert alloc == {"specific": 50.0, "nisa_growth": 50.0}
