"""dividend_history.py の純関数テスト。保存・通信は伴わない。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dividend_history as dh  # noqa: E402


ROWS = [
    {"date": "2025-03-28", "ticker": "9432", "name": "NTT", "gross": "5000",
     "tax": "1016", "net": "3984", "account": "specific", "source": "sbi"},
    {"date": "2025-09-26", "ticker": "9432", "name": "NTT", "gross": "5200",
     "tax": "1056", "net": "4144", "account": "specific", "source": "sbi"},
    {"date": "2026-03-27", "ticker": "9432", "name": "NTT", "gross": "5500",
     "tax": "0", "net": "5500", "account": "nisa_growth", "source": "sbi"},
    {"date": "2026-06-25", "ticker": "SCHD", "name": "SCHD", "gross": "12000",
     "tax": "1200", "net": "10800", "account": "nisa_growth", "source": "rakuten"},
]


def test_net_amount_is_filled_from_gross_minus_tax():
    """net が空でも手取りが出る（入力を1列省ける）。"""
    assert dh.net_amount({"gross": "5000", "tax": "1016"}) == pytest.approx(3984.0)
    assert dh.net_amount({"gross": "5000", "tax": "1016", "net": "4000"}) == pytest.approx(4000.0)


def test_normalize_fills_missing_columns_and_computes_net():
    got = dh.normalize({"date": "2026-03-27", "ticker": "9432", "gross": "5000", "tax": "0"})
    assert set(got) == set(dh.HISTORY_COLUMNS)
    assert got["net"] == "5000"
    assert got["note"] == ""


def test_parse_csv_drops_rows_without_date():
    text = ("date,ticker,gross,tax\n"
            "2026-03-27,9432,5000,0\n"
            ",8306,7000,1422\n")
    rows = dh.parse_csv(text)
    assert [r["ticker"] for r in rows] == ["9432"]


def test_parse_csv_accepts_formatted_numbers():
    """証券会社のCSVは桁区切りが入ることがある。"""
    rows = dh.parse_csv("date,ticker,gross,tax\n2026-03-27,9432,\"5,500\",0\n")
    assert rows[0]["net"] == "5500"


def test_serialize_and_parse_roundtrip():
    text = dh.serialize_csv(ROWS)
    back = dh.parse_csv(text)
    assert len(back) == len(ROWS)
    assert back[0]["date"] == "2025-03-28"   # 昇順で書かれる
    assert back[-1]["ticker"] == "SCHD"


def test_merge_is_idempotent_for_same_receipt():
    """同じCSVを2回取り込んでも二重計上しない。"""
    merged = dh.merge(ROWS, ROWS)
    assert len(merged) == len(ROWS)
    assert dh.by_year(merged)["2026"] == pytest.approx(5500 + 10800)


def test_merge_overwrites_with_imported_values():
    fixed = [{"date": "2026-03-27", "ticker": "9432", "account": "nisa_growth",
              "gross": "6000", "tax": "0"}]
    merged = dh.merge(ROWS, fixed)
    row = [r for r in merged if r["date"] == "2026-03-27"][0]
    assert row["gross"] == "6000"
    assert row["net"] == "6000"


def test_merge_keeps_same_ticker_in_different_accounts():
    """口座が違えば別の受取＝まとめてしまわないこと。"""
    other = [{"date": "2026-03-27", "ticker": "9432", "account": "specific",
              "gross": "1000", "tax": "203"}]
    merged = dh.merge(ROWS, other)
    same_day = [r for r in merged if r["date"] == "2026-03-27"]
    assert len(same_day) == 2


def test_by_year_defaults_to_net():
    """目標（月6〜10万）は手取りで見るため既定は net。"""
    assert dh.by_year(ROWS)["2025"] == pytest.approx(3984 + 4144)
    assert dh.by_year(ROWS, pre_tax=True)["2025"] == pytest.approx(5000 + 5200)


def test_by_month_can_filter_by_year():
    got = dh.by_month(ROWS, year="2026")
    assert got == pytest.approx({"2026-03": 5500.0, "2026-06": 10800.0})


def test_by_ticker_sums_across_accounts():
    got = dh.by_ticker(ROWS)
    assert got["9432"] == pytest.approx(3984 + 4144 + 5500)


def test_by_industry_uses_holdings_mapping_and_falls_back():
    got = dh.by_industry(ROWS, {"9432": "情報・通信業"}, year="2026")
    assert got["情報・通信業"] == pytest.approx(5500.0)
    assert got["未分類"] == pytest.approx(10800.0)  # 保有に無い銘柄（売却済み等）


def test_years_lists_recorded_years():
    assert dh.years(ROWS) == ["2025", "2026"]


def test_growth_rate_is_cagr_between_first_and_last_year():
    rows = [
        {"date": "2022-06-01", "ticker": "A", "gross": "100", "tax": "0"},
        {"date": "2025-06-01", "ticker": "A", "gross": "200", "tax": "0"},
    ]
    # 3年で2倍 → 約26%
    assert dh.growth_rate(rows) == pytest.approx(25.99, abs=0.05)


def test_growth_rate_needs_two_years_and_positive_base():
    assert dh.growth_rate([]) is None
    assert dh.growth_rate([{"date": "2026-01-01", "gross": "100", "tax": "0"}]) is None
    zero_start = [{"date": "2024-01-01", "gross": "0", "tax": "0"},
                  {"date": "2026-01-01", "gross": "100", "tax": "0"}]
    assert dh.growth_rate(zero_start) is None


def test_progress_against_plan_compares_current_year():
    # 2026 の手取り実績 16,300 に対し予定 20,000 → 81.5%
    assert dh.progress_against_plan(ROWS, 20000.0, "2026") == pytest.approx(81.5)


def test_progress_against_plan_without_plan_is_none():
    assert dh.progress_against_plan(ROWS, 0.0, "2026") is None
