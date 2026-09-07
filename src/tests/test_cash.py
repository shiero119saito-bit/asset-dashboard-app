"""cash.py の純関数テスト。現金は総資産・現金比率・運用比率の分母になる。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cash as ca  # noqa: E402


ROWS = [
    {"name": "楽天銀行", "amount": "1200000", "note": "生活防衛"},
    {"name": "住信SBI", "amount": "800000", "note": ""},
]


def test_total_sums_amounts():
    assert ca.total(ROWS) == pytest.approx(2_000_000.0)


def test_total_ignores_rows_without_name():
    """編集表の空行（名前なし）は数えない。"""
    rows = ROWS + [{"name": "", "amount": "999999", "note": ""}]
    assert ca.total(rows) == pytest.approx(2_000_000.0)


def test_parse_csv_accepts_formatted_numbers():
    rows = ca.parse_csv("name,amount,note\n楽天銀行,\"1,200,000\",生活防衛\n")
    assert rows[0]["amount"] == "1200000"


def test_parse_csv_empty_is_safe():
    assert ca.parse_csv(None) == []
    assert ca.parse_csv("") == []
    assert ca.parse_csv("name,amount,note\n") == []


def test_serialize_and_parse_roundtrip():
    back = ca.parse_csv(ca.serialize_csv(ROWS))
    assert [r["name"] for r in back] == ["楽天銀行", "住信SBI"]
    assert ca.total(back) == pytest.approx(2_000_000.0)


def test_serialize_drops_blank_rows():
    text = ca.serialize_csv(ROWS + [{"name": "", "amount": "1", "note": ""}])
    assert len(ca.parse_csv(text)) == 2


def test_net_worth_and_ratios():
    market = 8_000_000.0
    assert ca.net_worth(market, ROWS) == pytest.approx(10_000_000.0)
    assert ca.cash_ratio(market, ROWS) == pytest.approx(20.0)
    assert ca.invested_ratio(market, ROWS) == pytest.approx(80.0)


def test_ratios_without_cash_are_all_invested():
    """現金未入力なら総資産＝運用資産（0で割らない）。"""
    assert ca.net_worth(5_000_000.0, []) == pytest.approx(5_000_000.0)
    assert ca.cash_ratio(5_000_000.0, []) == pytest.approx(0.0)
    assert ca.invested_ratio(5_000_000.0, []) == pytest.approx(100.0)


def test_ratios_with_nothing_are_zero():
    assert ca.cash_ratio(0.0, []) == 0.0
    assert ca.invested_ratio(0.0, []) == 0.0
