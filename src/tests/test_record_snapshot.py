"""record_snapshot.py のテスト（通信はすべてスタブ・ファイル書込は tmp_path）。

主目的は2つ：
- **配当が円建てで記録されること**（米国銘柄をドルのまま入れると推移が壊れる）
- **同じ月に何度実行しても行が増えないこと**（cron と手動実行が両立する）
"""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts"))

import record_snapshot as rs  # noqa: E402
import snapshots as sn  # noqa: E402

ROWS = [
    {"ticker": "9432", "name": "NTT", "asset_class": "jp_dividend", "shares": "100",
     "cost_per_share": "150", "market": "jp", "account": "specific", "price": "160",
     "div_per_share": ""},
    {"ticker": "SCHD", "name": "SCHD", "asset_class": "us_dividend", "shares": "10",
     "cost_per_share": "4000", "market": "us", "account": "nisa_growth", "price": "5000",
     "div_per_share": ""},
]

HOLDINGS_CSV_TEXT = (
    "ticker,name,asset_class,shares,cost_per_share,market,account,price,div_per_share\n"
    "9432,NTT,jp_dividend,100,150,jp,specific,160,\n"
    "SCHD,SCHD,us_dividend,10,4000,us,nisa_growth,5000,\n"
)


def _stub_dividends(monkeypatch, dividends, fx):
    monkeypatch.setattr(rs.pr, "fetch_dividends", lambda tickers: dict(dividends))
    monkeypatch.setattr(rs.pr, "fetch_fx_rate", lambda: fx)


def test_us_dividend_is_converted_to_yen(monkeypatch):
    """yfinance は米国銘柄の配当をドルで返す。円建てにしてから記録する。"""
    _stub_dividends(monkeypatch, {"9432": 5.0, "SCHD": 1.0}, fx=150.0)
    div_map = rs.fetch_missing_dividends(ROWS, {})
    assert div_map["SCHD"] == pytest.approx(150.0)
    assert div_map["9432"] == pytest.approx(5.0)


def test_us_dividend_is_dropped_when_fx_unavailable(monkeypatch):
    """為替が取れないときは米国銘柄を落とす＝ドル建ての値を記録しない。"""
    _stub_dividends(monkeypatch, {"9432": 5.0, "SCHD": 1.0}, fx=None)
    div_map = rs.fetch_missing_dividends(ROWS, {})
    assert "SCHD" not in div_map
    assert div_map["9432"] == pytest.approx(5.0)


def test_csv_value_wins_over_fetch(monkeypatch):
    """div_per_share が入っている銘柄は取得しに行かない（手入力を上書きしない）。"""
    called = []
    monkeypatch.setattr(rs.pr, "fetch_dividends", lambda tickers: called.append(tickers) or {})
    monkeypatch.setattr(rs.pr, "fetch_fx_rate", lambda: 150.0)
    div_map = rs.fetch_missing_dividends(ROWS, {"9432": 7.0})
    assert div_map["9432"] == 7.0
    assert called == [["SCHD"]]


def test_build_uses_price_column_not_live_prices(monkeypatch):
    """時価は price 列を使う（時価更新の直後に走る前提。二重取得しない）。"""
    _stub_dividends(monkeypatch, {}, fx=150.0)
    record = rs.build(ROWS, date(2026, 9, 7), fetch=False)
    assert record["total_market"] == 100 * 160 + 10 * 5000
    assert record["total_cost"] == 100 * 150 + 10 * 4000


def test_run_local_writes_and_overwrites_same_month(tmp_path, monkeypatch, capsys):
    holdings = tmp_path / "holdings.csv"
    holdings.write_text(HOLDINGS_CSV_TEXT, encoding="utf-8")
    out = tmp_path / "snapshots.csv"

    assert rs.run_local(str(holdings), str(out), date(2026, 9, 7), dry_run=False, fetch=False) == 0
    assert len(sn.parse_csv(out.read_text(encoding="utf-8"))) == 1

    # 同じ月にもう一度：行は増えず、日付だけが後の実行で置き換わる
    assert rs.run_local(str(holdings), str(out), date(2026, 9, 30), dry_run=False, fetch=False) == 0
    rows = sn.parse_csv(out.read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-09-30"

    # 翌月は追加される
    assert rs.run_local(str(holdings), str(out), date(2026, 10, 1), dry_run=False, fetch=False) == 0
    assert len(sn.parse_csv(out.read_text(encoding="utf-8"))) == 2


def test_run_local_dry_run_does_not_write(tmp_path):
    holdings = tmp_path / "holdings.csv"
    holdings.write_text(HOLDINGS_CSV_TEXT, encoding="utf-8")
    out = tmp_path / "snapshots.csv"
    assert rs.run_local(str(holdings), str(out), date(2026, 9, 7), dry_run=True, fetch=False) == 0
    assert not out.exists()


def test_run_local_missing_file_fails(tmp_path):
    missing = tmp_path / "nope.csv"
    out = tmp_path / "snapshots.csv"
    assert rs.run_local(str(missing), str(out), date(2026, 9, 7), dry_run=False, fetch=False) == 1
