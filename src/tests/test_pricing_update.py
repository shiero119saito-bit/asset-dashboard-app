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
