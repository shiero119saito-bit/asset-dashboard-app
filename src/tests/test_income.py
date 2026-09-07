"""income.py の純関数テスト。保存・通信は伴わない。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import income as inc  # noqa: E402


ROWS = [
    {"month": "2026-07", "category": "事業", "amount": "40000", "note": ""},
    {"month": "2026-07", "category": "労働収入", "amount": "60000", "note": "週2"},
    {"month": "2026-08", "category": "事業", "amount": "50000", "note": ""},
    {"month": "2026-09", "category": "事業", "amount": "60000", "note": ""},
]


def test_normalize_rounds_month_and_amount():
    got = inc.normalize({"month": "2026-09-15", "category": "事業", "amount": "50,000"})
    assert got["month"] == "2026-09"
    assert got["amount"] == "50000"
    assert set(got) == set(inc.INCOME_COLUMNS)


def test_parse_csv_drops_rows_without_month():
    text = "month,category,amount\n2026-09,事業,50000\n,事業,99999\n"
    assert [r["amount"] for r in inc.parse_csv(text)] == ["50000"]


def test_parse_csv_empty_is_safe():
    assert inc.parse_csv(None) == []
    assert inc.parse_csv("") == []


def test_serialize_and_parse_roundtrip():
    back = inc.parse_csv(inc.serialize_csv(ROWS))
    assert len(back) == 4
    assert back[0]["month"] == "2026-07"


def test_merge_is_idempotent_per_month_and_category():
    """同じ月・区分は上書き＝取込を繰り返しても二重計上しない。"""
    merged = inc.merge(ROWS, ROWS)
    assert len(merged) == len(ROWS)


def test_merge_overwrites_same_month_and_category():
    fixed = [{"month": "2026-09", "category": "事業", "amount": "70000"}]
    merged = inc.merge(ROWS, fixed)
    september = [r for r in merged if r["month"] == "2026-09" and r["category"] == "事業"]
    assert len(september) == 1
    assert september[0]["amount"] == "70000"


def test_by_month_can_filter_by_category():
    assert inc.by_month(ROWS, "事業") == pytest.approx(
        {"2026-07": 40000.0, "2026-08": 50000.0, "2026-09": 60000.0}
    )
    assert inc.by_month(ROWS)["2026-07"] == pytest.approx(100000.0)


def test_by_category_covers_all_categories():
    got = inc.by_category(ROWS)
    assert got[inc.BUSINESS] == pytest.approx(150000.0)
    assert got[inc.LABOR] == pytest.approx(60000.0)


def test_by_category_for_one_month():
    got = inc.by_category(ROWS, month="2026-08")
    assert got[inc.BUSINESS] == pytest.approx(50000.0)
    assert got[inc.LABOR] == 0.0   # 実績なし＝0（キーは残す）


def test_recent_average_counts_months_without_records_as_zero():
    """稼働した月だけの平均にすると実力を過大評価する。空白月は0で数える。"""
    rows = [
        {"month": "2026-07", "category": "労働収入", "amount": "60000"},
        {"month": "2026-09", "category": "労働収入", "amount": "60000"},  # 8月は記録なし
    ]
    # 3か月（7・8・9）で12万 → 月平均4万
    assert inc.recent_average(rows, inc.LABOR) == pytest.approx(40000.0)


def test_recent_average_empty_is_zero():
    assert inc.recent_average([], inc.BUSINESS) == 0.0


def test_recent_average_respects_span():
    rows = [{"month": f"2025-{m:02d}", "category": "事業", "amount": "10000"} for m in range(1, 13)]
    rows += [{"month": "2026-01", "category": "事業", "amount": "70000"}]
    # 直近3か月＝2025-11・2025-12・2026-01 の (1万+1万+7万)/3 = 3万
    assert inc.recent_average(rows, inc.BUSINESS, span=3) == pytest.approx(30000.0)


def test_progress_against_monthly_goal():
    assert inc.progress(40000.0, 50000.0) == pytest.approx(80.0)
    assert inc.progress(40000.0, 0.0) == 0.0
