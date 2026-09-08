"""pricing_update.py のテスト（純関数・外部依存なし）。"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pricing_update as pu  # noqa: E402

TODAY = date(2026, 9, 5)


def _rows() -> list[dict]:
    return [
        {"ticker": "1343", "name": "REIT", "price": "1800", "price_asof": "2026-08-01"},
        {"ticker": "SCHD", "name": "SCHD", "price": "", "price_asof": ""},
        {"ticker": "ｅＭＡＸＩＳ", "name": "投信", "price": "12345", "price_asof": "2026-07-01"},
    ]


# --- apply_prices ---


def test_updates_only_fetched_tickers():
    rows, updated = pu.apply_prices(_rows(), {"1343": 1931.0, "SCHD": 5500.0}, TODAY)
    assert updated == 2
    assert (rows[0]["price"], rows[0]["price_asof"]) == ("1931", "2026-09-05")
    assert (rows[1]["price"], rows[1]["price_asof"]) == ("5500", "2026-09-05")


def test_keeps_existing_value_when_not_fetched():
    """取得できない銘柄（投資信託・通信失敗）は古い値を残す。

    時価ゼロで評価額が消える方が、古い値を使うより害が大きい。
    """
    rows, _ = pu.apply_prices(_rows(), {"1343": 1931.0}, TODAY)
    assert rows[2]["price"] == "12345"
    assert rows[2]["price_asof"] == "2026-07-01"


def test_empty_price_map_changes_nothing():
    rows, updated = pu.apply_prices(_rows(), {}, TODAY)
    assert updated == 0
    assert rows == _rows()


def test_same_value_on_same_day_is_not_counted_as_update():
    """値も日付も変わらないなら更新扱いにしない（無意味なコミットを生まないため）。"""
    rows = [{"ticker": "1343", "price": "1931", "price_asof": "2026-09-05"}]
    _, updated = pu.apply_prices(rows, {"1343": 1931.0}, TODAY)
    assert updated == 0


def test_same_price_on_a_new_day_counts_as_update():
    # 値が同じでも日付は進める＝「いつ時点の評価か」を画面に出せるようにする
    rows = [{"ticker": "1343", "price": "1931", "price_asof": "2026-09-04"}]
    rows, updated = pu.apply_prices(rows, {"1343": 1931.0}, TODAY)
    assert updated == 1 and rows[0]["price_asof"] == "2026-09-05"


def test_does_not_mutate_input():
    # 呼び出し側が更新前後を比較できるよう、引数は書き換えない
    original = _rows()
    pu.apply_prices(original, {"1343": 9999.0}, TODAY)
    assert original[0]["price"] == "1800"


def test_ticker_is_matched_after_trimming():
    rows = [{"ticker": " 1343 ", "price": "", "price_asof": ""}]
    rows, updated = pu.apply_prices(rows, {"1343": 1931.0}, TODAY)
    assert updated == 1 and rows[0]["price"] == "1931"


def test_zero_price_is_ignored():
    # 0 は「取得できた」ではなく異常値。既存の値を壊さない
    rows, updated = pu.apply_prices(_rows(), {"1343": 0.0}, TODAY)
    assert updated == 0 and rows[0]["price"] == "1800"


def test_format_price_trims_trailing_zeros():
    assert pu.format_price(1931.0) == "1931"
    assert pu.format_price(55.125) == "55.125"
    assert pu.format_price(0.5) == "0.5"


# --- stale_days ---


def test_stale_days_uses_latest_asof():
    """行ごとに日付が違いうるので、最も新しい日＝最後に更新を回した日を基準にする。"""
    rows = [
        {"ticker": "A", "price_asof": "2026-07-01"},
        {"ticker": "B", "price_asof": "2026-09-03"},
    ]
    assert pu.stale_days(rows, TODAY) == 2


def test_stale_days_is_zero_when_updated_today():
    assert pu.stale_days([{"price_asof": "2026-09-05"}], TODAY) == 0


def test_stale_days_none_when_no_prices():
    assert pu.stale_days([{"ticker": "A"}, {"price_asof": ""}], TODAY) is None


def test_stale_days_ignores_broken_values():
    # 手編集で壊れた日付が入っても画面を落とさない
    rows = [{"price_asof": "nan"}, {"price_asof": "2026/09/03"}, {"price_asof": "2026-09-01"}]
    assert pu.stale_days(rows, TODAY) == 4


def test_stale_days_never_negative():
    # 端末の時計ずれで未来日が入っても負数を返さない
    assert pu.stale_days([{"price_asof": "2026-09-10"}], TODAY) == 0


def test_stale_days_reads_the_requested_column():
    """配当（div_asof）の鮮度にも同じ関数を使う＝鮮度判定を2つ持たない。"""
    rows = [{"price_asof": "2026-09-05", "div_asof": "2026-08-29"}]
    assert pu.stale_days(rows, TODAY) == 0
    assert pu.stale_days(rows, TODAY, pu.DIVIDEND_ASOF_COLUMN) == 7


# --- 権利確定月の表記 ---


def test_months_round_trip():
    assert pu.format_months([9, 3]) == "3;9"          # 昇順で書く
    assert pu.parse_months("3;9") == [3, 9]


def test_format_months_is_stable_against_order_and_duplicates():
    """取得順の揺れで無意味な差分＝無意味なコミットを作らない。"""
    assert pu.format_months([9, 3, 9]) == pu.format_months([3, 9]) == "3;9"


def test_format_months_drops_out_of_range():
    assert pu.format_months([0, 13, 6]) == "6"


def test_parse_months_ignores_broken_values():
    # 手編集で壊れた値が入っても画面を落とさない
    assert pu.parse_months("3;x;13;9") == [3, 9]
    assert pu.parse_months("") == []
    assert pu.parse_months(None) == []


# --- apply_dividends ---


def _div_rows() -> list[dict]:
    return [
        {"ticker": "1343", "div_annual": "60", "div_months": "3;9", "div_asof": "2026-08-01"},
        {"ticker": "VYM", "div_annual": "", "div_months": "", "div_asof": ""},
        {"ticker": "ｅＭＡＸＩＳ", "div_annual": "12", "div_months": "", "div_asof": "2026-08-01"},
    ]


def test_apply_dividends_writes_annual_and_months():
    rows, updated = pu.apply_dividends(
        _div_rows(), {"VYM": 550.0}, {"VYM": [3, 6, 9, 12]}, TODAY
    )
    assert updated == 1
    assert rows[1]["div_annual"] == "550"
    assert rows[1]["div_months"] == "3;6;9;12"
    assert rows[1]["div_asof"] == "2026-09-05"


def test_apply_dividends_keeps_existing_when_not_fetched():
    """取得できなかった銘柄は既存値を残す（古い値でも無いよりまし）。"""
    rows, updated = pu.apply_dividends(_div_rows(), {}, {}, TODAY)
    assert updated == 0
    assert rows[0]["div_annual"] == "60" and rows[0]["div_asof"] == "2026-08-01"


def test_apply_dividends_never_touches_manual_column():
    """手入力（div_per_share）を定期取得が潰さないこと。この保証が列を分けた理由。"""
    rows = [{"ticker": "1605", "div_per_share": "70", "div_annual": ""}]
    updated_rows, _ = pu.apply_dividends(rows, {"1605": 62.0}, {"1605": [3]}, TODAY)
    assert updated_rows[0]["div_per_share"] == "70"   # 手入力はそのまま
    assert updated_rows[0]["div_annual"] == "62"      # 自動取得は別列に入る


def test_apply_dividends_advances_asof_even_when_value_is_unchanged():
    """値が同じでも「いつ確かめたか」は進める＝画面の鮮度表示が先週のままに見えない。"""
    rows, updated = pu.apply_dividends(_div_rows(), {"1343": 60.0}, {"1343": [3, 9]}, TODAY)
    assert updated == 1
    assert rows[0]["div_asof"] == "2026-09-05"


def test_apply_dividends_does_not_mutate_input():
    original = _div_rows()
    pu.apply_dividends(original, {"VYM": 550.0}, {"VYM": [3]}, TODAY)
    assert original[1]["div_annual"] == ""


# --- 読み出し（画面・スナップショット記録の共通入口）---


def test_dividend_map_prefers_manual_over_fetched():
    """手入力が自動取得に勝つ。ライブ取得時代の優先順位をそのまま保存に移したもの。"""
    rows = [{"ticker": "1605", "div_per_share": "70", "div_annual": "62"}]
    assert pu.dividend_map(rows) == {"1605": 70.0}


def test_dividend_map_falls_back_to_fetched():
    rows = [{"ticker": "1605", "div_per_share": "", "div_annual": "62"}]
    assert pu.dividend_map(rows) == {"1605": 62.0}


def test_dividend_map_skips_empty_and_broken_values():
    rows = [
        {"ticker": "A", "div_per_share": "nan", "div_annual": ""},
        {"ticker": "B", "div_per_share": "", "div_annual": "0"},   # 0は配当なし＝キーを作らない
        {"ticker": "C", "div_per_share": "x", "div_annual": "5"},  # 壊れた手入力は自動取得へ
    ]
    assert pu.dividend_map(rows) == {"C": 5.0}


def test_dividend_months_map_skips_blank_rows():
    rows = [{"ticker": "A", "div_months": "3;9"}, {"ticker": "B", "div_months": ""}]
    assert pu.dividend_months_map(rows) == {"A": [3, 9]}
