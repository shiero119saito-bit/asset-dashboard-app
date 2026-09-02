"""dataio.py の純関数テスト（I/O なし）。"""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dataio  # noqa: E402
import portfolio as pf  # noqa: E402


FULL_CSV = (
    "ticker,name,asset_class,shares,cost_per_share,sector,market,div_per_share\n"
    "1489,日経高配当50,jp_dividend,30,55000,分散ETF,jp,2200\n"
    "SCHD,Schwab US Dividend,us_dividend,60,2600,分散ETF,us,110\n"
)

MINIMAL_CSV = (
    "ticker,name,asset_class,shares,cost_per_share\n"
    "2559,オルカン,index,100,15000\n"
)


def test_parse_full_csv():
    rows = dataio.parse_holdings_csv(FULL_CSV)
    assert len(rows) == 2
    assert rows[0]["ticker"] == 1489 or str(rows[0]["ticker"]) == "1489"
    assert rows[1]["sector"] == "分散ETF"
    # 追加列を含めて build_holdings まで通ること
    holdings = pf.build_holdings(rows, {})
    assert {h.market for h in holdings} == {"jp", "us"}


def test_parse_minimal_csv_optional_columns_absent():
    # 追加列が無くても必須列が揃えば parse でき、build_holdings がデフォルト補完
    rows = dataio.parse_holdings_csv(MINIMAL_CSV)
    holdings = pf.build_holdings(rows, {})
    assert holdings[0].sector == "その他"
    assert holdings[0].market == "jp"  # 4桁数字→jp 推定


def test_missing_required_column_raises():
    bad = "ticker,name,asset_class,shares\n2559,オルカン,index,100\n"  # cost_per_share 欠落
    with pytest.raises(ValueError):
        dataio.parse_holdings_csv(bad)


def test_empty_input_returns_empty():
    assert dataio.parse_holdings_csv("") == []
    assert dataio.parse_holdings_csv("   ") == []
    assert dataio.parse_holdings_csv(None) == []


# --- 個人設定（生年月日）---


def test_birth_date_roundtrip():
    birth = date(1980, 6, 15)
    assert dataio.parse_birth_date(dataio.serialize_birth_date(birth)) == birth


def test_parse_birth_date_returns_none_for_unset_or_broken():
    # 未設定・空・壊れたJSON・キー欠損・不正な日付は例外でなく None（＝未設定扱い）
    assert dataio.parse_birth_date(None) is None
    assert dataio.parse_birth_date("") is None
    assert dataio.parse_birth_date("   ") is None
    assert dataio.parse_birth_date("{壊れている") is None
    assert dataio.parse_birth_date('{"other_key": 1}') is None
    assert dataio.parse_birth_date('{"birth_date": "1980-13-99"}') is None
    assert dataio.parse_birth_date('{"birth_date": null}') is None


# --- CSV シリアライズ（storage への保存用）---


def test_serialize_holdings_csv_roundtrip():
    # parse → serialize → parse で内容が保たれること
    rows = dataio.parse_holdings_csv(FULL_CSV)
    text = dataio.serialize_holdings_csv(rows)
    again = dataio.parse_holdings_csv(text)
    for before, after in zip(rows, again):
        assert str(before["ticker"]) == str(after["ticker"])
        assert float(before["shares"]) == float(after["shares"])
        assert str(before["name"]) == str(after["name"])


def test_serialize_holdings_csv_uses_canonical_column_order():
    from dataio import HOLDINGS_COLUMNS

    text = dataio.serialize_holdings_csv([{"ticker": "1605", "name": "INPEX"}])
    assert text.split("\n")[0] == ",".join(HOLDINGS_COLUMNS)


def test_serialize_holdings_csv_writes_empty_for_missing_and_nan():
    # pandas が空欄を float('nan') で読むため、素直に str() すると "nan" が残る
    # （実際に画面へ「nan 時点」と表示される事故が起きたため固定する）
    rows = [{"ticker": "1605", "name": "INPEX", "purpose": float("nan"),
             "price": None, "source": "nan"}]
    text = dataio.serialize_holdings_csv(rows)
    assert "nan" not in text
    data_line = text.split("\n")[1]
    assert data_line.startswith("1605,INPEX,")
    assert data_line.endswith(",,,")  # 末尾の未設定列は空欄で埋まる


def test_serialize_empty_rows_writes_header_only():
    from dataio import HOLDINGS_COLUMNS

    text = dataio.serialize_holdings_csv([])
    assert text.strip() == ",".join(HOLDINGS_COLUMNS)


def test_edit_roundtrip_preserves_changes_and_additions():
    """画面編集の往復：値の変更・分類の設定・行追加が保存を経ても保たれること。

    data_editor → serialize → storage → parse という実際の経路を模擬する。
    """
    import portfolio as pf

    rows = dataio.parse_holdings_csv(FULL_CSV)
    edited = [dict(r) for r in rows]
    edited[0]["shares"] = 999
    edited[0]["purpose"] = "yutai"
    edited.append({
        "ticker": "9999", "name": "追加銘柄", "asset_class": "jp_dividend",
        "shares": 10, "cost_per_share": 1000, "sector": "個別株", "market": "jp",
        "purpose": "dividend", "source": "手入力",
    })

    back = dataio.parse_holdings_csv(dataio.serialize_holdings_csv(edited))
    assert len(back) == len(rows) + 1
    assert float(back[0]["shares"]) == 999
    assert back[0]["purpose"] == "yutai"
    assert back[-1]["ticker"] == "9999"

    # 保存後のデータがそのまま集計に使えること（分類が効く）
    holdings = pf.build_holdings(back, {})
    groups = pf.jp_dividend_by_purpose(holdings)
    assert any(h.ticker == "9999" for h in groups["dividend"])
