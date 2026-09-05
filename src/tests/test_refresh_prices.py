"""refresh_prices.py の時価取得部分のテスト（通信はすべてスタブ）。

回帰テストの主目的は **price 列に必ず円建ての値が入ること**。
2026-09-05、米国銘柄をドル建てのまま保存していたため VYM 17株が 2,792円と評価され、
実際より約50万円少ない総評価額になっていた（家計簿との突き合わせで発覚）。
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts"))

import refresh_prices as rp  # noqa: E402

ROWS = [
    {"ticker": "1343", "market": "jp", "price": "1800", "price_asof": "2026-08-01"},
    {"ticker": "VYM", "market": "us", "price": "24000", "price_asof": "2026-08-01"},
    {"ticker": "オルカン", "market": "jp", "price": "2.4", "price_asof": "2026-08-01",
     "isin": "JP90C000H1T1", "assoc_fund_cd": "0331418A"},
]


def _stub_prices(monkeypatch, prices, fx):
    monkeypatch.setattr(rp.pr, "fetch_prices", lambda tickers: dict(prices))
    monkeypatch.setattr(rp.pr, "fetch_fx_rate", lambda: fx)


# --- 通貨換算（欠陥の回帰テスト）---


def test_us_price_is_converted_to_yen(monkeypatch):
    """yfinance は米国銘柄をドルで返す。円建てにしてから price 列へ書く。"""
    _stub_prices(monkeypatch, {"1343": 1931.0, "VYM": 164.23}, fx=150.0)
    price_map, _ = rp.fetch_price_map(ROWS)
    assert price_map["VYM"] == 164.23 * 150.0
    assert price_map["1343"] == 1931.0  # 日本株は素通し


def test_us_price_is_dropped_when_fx_unavailable(monkeypatch):
    """為替が取れないときは米国銘柄を更新しない＝ドル建ての値が残らない。

    0 や生のドル値を書くくらいなら、古い円建ての値を残すほうが害が小さい。
    """
    _stub_prices(monkeypatch, {"1343": 1931.0, "VYM": 164.23}, fx=None)
    price_map, _ = rp.fetch_price_map(ROWS)
    assert "VYM" not in price_map
    assert price_map["1343"] == 1931.0


def test_no_fx_call_when_no_us_holdings(monkeypatch):
    # 日本株だけなら為替は引かない（無駄な通信をしない）
    called = []
    monkeypatch.setattr(rp.pr, "fetch_prices", lambda tickers: {"1343": 1931.0})
    monkeypatch.setattr(rp.pr, "fetch_fx_rate", lambda: called.append(1) or 150.0)
    rp.fetch_price_map([ROWS[0]])
    assert called == []


def test_saved_price_stays_in_yen_end_to_end(monkeypatch):
    """取得から行への書き込みまで通して、円建てで保存されること。"""
    _stub_prices(monkeypatch, {"VYM": 164.23}, fx=150.0)
    monkeypatch.setattr(rp, "fundprices_for", lambda funds: {})
    rows, updated, fetched = rp.compute_updates([ROWS[1]])
    assert fetched and updated == 1
    assert float(rows[0]["price"]) == 164.23 * 150.0


# --- 投資信託 ---


def test_fund_codes_requires_both_columns():
    rows = [
        {"ticker": "A", "isin": "X", "assoc_fund_cd": "1"},
        {"ticker": "B", "isin": "X"},                      # 片方だけ
        {"ticker": "C", "isin": "nan", "assoc_fund_cd": "nan"},  # pandas の欠損
        {"ticker": "D"},
    ]
    assert rp.fund_codes(rows) == {"A": ("X", "1")}


def test_funds_are_updated_even_though_yfinance_skips_them(monkeypatch):
    """投信は yfinance に存在しない（is_fetchable が false）が、isin があれば更新される。

    ここが抜けていたため、投信9本が取得単価のまま＝含み益が丸ごと欠落していた。
    """
    _stub_prices(monkeypatch, {}, fx=None)
    monkeypatch.setattr(rp, "fundprices_for", lambda funds: {"オルカン": 3.7945})
    rows, updated, fetched = rp.compute_updates([ROWS[2]])
    assert fetched and updated == 1
    assert float(rows[0]["price"]) == 3.7945
    assert rows[0]["price_asof"] == date.today().isoformat()


def test_listed_and_funds_are_merged(monkeypatch):
    _stub_prices(monkeypatch, {"1343": 1931.0}, fx=150.0)
    monkeypatch.setattr(rp, "fundprices_for", lambda funds: {"オルカン": 3.7945})
    rows, updated, _ = rp.compute_updates(ROWS[:1] + ROWS[2:])
    assert updated == 2


def test_all_failures_report_not_fetched(monkeypatch):
    # 「取得できない」と「値動きなし」を区別する（沈黙する失敗を作らない）
    _stub_prices(monkeypatch, {}, fx=None)
    monkeypatch.setattr(rp, "fundprices_for", lambda funds: {})
    _, updated, fetched = rp.compute_updates(ROWS)
    assert updated == 0 and fetched is False


def test_missing_count_handles_same_ticker_in_multiple_brokers(monkeypatch):
    """同じ銘柄を複数の証券会社で持つ行を「取得できず」と誤カウントしないこと。

    行数から price_map の件数を引くと、1つの price が複数行を更新する分だけ
    取れているのに未取得として数えられる（実データで13件の誤報が出た）。
    """
    rows = [
        {"ticker": "1343", "market": "jp", "source": "sbi"},
        {"ticker": "1343", "market": "jp", "source": "rakuten"},
    ]
    _stub_prices(monkeypatch, {"1343": 1931.0}, fx=None)
    monkeypatch.setattr(rp, "fundprices_for", lambda funds: {})
    printed = []
    monkeypatch.setattr("builtins.print", lambda *a, **kw: printed.append(" ".join(map(str, a))))
    rp.compute_updates(rows)
    assert "取得できず=0件" in printed[0]
