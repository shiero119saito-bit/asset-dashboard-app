"""snapshots.py の純関数テスト。保存・通信は伴わない。"""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import portfolio as pf  # noqa: E402
import snapshots as sn  # noqa: E402


ROWS = [
    {"ticker": "2559", "name": "オルカン", "asset_class": "index", "shares": 100,
     "cost_per_share": 200, "market": "jp", "account": "nisa_tsumitate"},
    {"ticker": "9432", "name": "NTT", "asset_class": "jp_dividend", "shares": 100,
     "cost_per_share": 100, "market": "jp", "account": "specific"},
]
PRICES = {"2559": 250, "9432": 150}   # 評価額 25000 / 15000
DIV = {"9432": 5.0}                   # 年間配当 500（特定口座＝課税）


@pytest.fixture
def holdings():
    return pf.build_holdings(ROWS, PRICES)


def test_build_record_has_every_column(holdings):
    record = sn.build_record(holdings, DIV, on=date(2026, 9, 7))
    assert set(record) == set(sn.SNAPSHOT_COLUMNS)
    assert record["date"] == "2026-09-07"
    assert record["total_cost"] == 30000
    assert record["total_market"] == 40000
    assert record["gain"] == 10000


def test_build_record_keeps_both_tax_bases(holdings):
    """税抜は口座区分を反映する。NISA比率が高いほど税込との差が縮む。"""
    record = sn.build_record(holdings, DIV, on=date(2026, 9, 7))
    assert record["annual_dividend_pre_tax"] == 500
    assert record["annual_dividend_after_tax"] == round(500 * (1 - 0.20315))


def test_build_record_stores_allocation(holdings):
    record = sn.build_record(holdings, DIV, on=date(2026, 9, 7))
    assert record["index_pct"] == pytest.approx(62.5)
    assert record["jp_dividend_pct"] == pytest.approx(37.5)
    assert record["us_dividend_pct"] == 0.0
    assert record["reit_pct"] == 0.0


def test_upsert_replaces_same_month():
    """月内に何度記録しても1行。cron を月初に置いても手で押しても結果が同じになる。"""
    rows = [{"date": "2026-08-01", "total_market": "100"},
            {"date": "2026-09-01", "total_market": "200"}]
    got = sn.upsert(rows, {"date": "2026-09-20", "total_market": "250"})
    assert [r["date"] for r in got] == ["2026-08-01", "2026-09-20"]
    assert got[-1]["total_market"] == "250"


def test_upsert_appends_new_month():
    rows = [{"date": "2026-09-01", "total_market": "200"}]
    got = sn.upsert(rows, {"date": "2026-10-01", "total_market": "300"})
    assert [r["date"] for r in got] == ["2026-09-01", "2026-10-01"]


def test_serialize_and_parse_roundtrip(holdings):
    record = sn.build_record(holdings, DIV, on=date(2026, 9, 7))
    text = sn.serialize_csv([record])
    back = sn.parse_csv(text)
    assert len(back) == 1
    assert back[0]["date"] == "2026-09-07"
    assert back[0]["total_market"] == "40000"


def test_parse_csv_empty_is_safe():
    assert sn.parse_csv(None) == []
    assert sn.parse_csv("") == []
    assert sn.parse_csv("date,total_market\n") == []


def test_parse_csv_ignores_unknown_columns_and_blank_dates():
    """列を足した将来のCSVを古いコードが読んでも落ちないこと。"""
    text = "date,total_market,new_column\n2026-09-01,100,x\n,999,y\n"
    rows = sn.parse_csv(text)
    assert len(rows) == 1
    assert rows[0]["total_market"] == "100"
    assert "new_column" not in rows[0]


def test_series_is_sorted_by_date():
    rows = [{"date": "2026-09-01", "total_market": "200"},
            {"date": "2026-07-01", "total_market": "100"}]
    labels, values = sn.series(rows, "total_market")
    assert labels == ["2026-07-01", "2026-09-01"]
    assert values == [100.0, 200.0]


def test_change_from_previous_returns_delta_and_rate():
    rows = [{"date": "2026-08-01", "total_market": "1000"},
            {"date": "2026-09-01", "total_market": "1100"}]
    delta, rate = sn.change_from_previous(rows, "total_market")
    assert delta == pytest.approx(100.0)
    assert rate == pytest.approx(10.0)


def test_change_from_previous_needs_two_points():
    assert sn.change_from_previous([], "total_market") is None
    assert sn.change_from_previous([{"date": "2026-09-01", "total_market": "1"}],
                                   "total_market") is None


def test_change_from_previous_zero_base_does_not_explode():
    rows = [{"date": "2026-08-01", "annual_dividend_pre_tax": "0"},
            {"date": "2026-09-01", "annual_dividend_pre_tax": "500"}]
    delta, rate = sn.change_from_previous(rows, "annual_dividend_pre_tax")
    assert delta == pytest.approx(500.0)
    assert rate == 0.0


def test_latest_returns_newest_row():
    rows = [{"date": "2026-09-01"}, {"date": "2026-07-01"}]
    assert sn.latest(rows)["date"] == "2026-09-01"
    assert sn.latest([]) is None
