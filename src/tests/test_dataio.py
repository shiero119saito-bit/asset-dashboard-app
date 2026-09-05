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


def test_serialize_writes_whole_numbers_without_decimal_point():
    """整数値の float は整数として書く（69991.0 → 69991）。

    pandas が数値列を float で読むため、素直に str() すると株数や時価に不要な .0 が
    付く。小数を持つ値（投信の1口あたり取得単価）はそのまま残す必要がある。
    """
    text = dataio.serialize_holdings_csv([
        {"ticker": "1343", "shares": 40.0, "cost_per_share": 1800.0, "price": 1931.0},
        {"ticker": "オルカン", "shares": 366448.0, "cost_per_share": 2.469655, "price": 3.7945},
    ])
    lines = text.strip().split("\n")
    assert ",40,1800," in lines[1] and ",1931," in lines[1]
    assert ",366448,2.469655," in lines[2] and ",3.7945," in lines[2]


def test_holdings_columns_include_fund_codes():
    # 投信の基準価額取得には ISIN と協会コードの両方が要る
    assert dataio.HOLDINGS_COLUMNS[-2:] == ("isin", "assoc_fund_cd")


def test_rows_without_fund_columns_still_serialize():
    # 既存データ（isin 列を持たない）が後方互換で通ること
    text = dataio.serialize_holdings_csv([{"ticker": "1343", "shares": 40}])
    assert text.strip().split("\n")[1].endswith(",,")


# --- 口座区分（複合キーと分類の引き継ぎ）---


def test_merge_keeps_same_ticker_in_different_accounts():
    """同一銘柄でも口座が違えば別の行として残す（税率が違うため）。"""
    existing = [{
        "ticker": "1605", "name": "INPEX", "asset_class": "jp_dividend", "shares": "10",
        "cost_per_share": "1500", "source": "rakuten", "account": "specific",
    }]
    imported = [
        {"ticker": "1605", "name": "INPEX", "shares": 5.0, "cost_per_share": 1800.0,
         "account": "nisa_growth"},
    ]
    merged = dataio.merge_holdings(existing, imported, source="rakuten")
    by_account = {row["account"]: row for row in merged}
    assert set(by_account) == {"specific", "nisa_growth"}
    assert by_account["specific"]["shares"] == "10"
    assert by_account["nisa_growth"]["shares"] == 5.0


def test_merge_inherits_classification_when_row_splits_by_account():
    """再取込で1行が口座別に分かれても、分類と ISIN を引き継ぐこと。

    引き継がないと「新規銘柄」として既定分類になり、purpose も isin も消える。
    isin が消えると投資信託の基準価額が取れなくなり、評価額が取得単価に戻る。
    """
    existing = [{
        "ticker": "オルカン", "name": "オルカン", "asset_class": "index", "shares": "100",
        "cost_per_share": "2.4", "sector": "投資信託", "market": "jp", "div_per_share": "",
        "purpose": "growth", "source": "rakuten", "account": "specific",
        "isin": "JP90C000H1T1", "assoc_fund_cd": "0331418A",
    }]
    imported = [
        {"ticker": "オルカン", "name": "オルカン", "shares": 60.0, "cost_per_share": 2.4,
         "account": "nisa_tsumitate"},
        {"ticker": "オルカン", "name": "オルカン", "shares": 40.0, "cost_per_share": 2.5,
         "account": "nisa_old"},
    ]
    merged = dataio.merge_holdings(existing, imported, source="rakuten")
    new_rows = [r for r in merged if r["account"] in ("nisa_tsumitate", "nisa_old")]
    assert len(new_rows) == 2
    for row in new_rows:
        assert row["isin"] == "JP90C000H1T1"
        assert row["assoc_fund_cd"] == "0331418A"
        assert row["asset_class"] == "index"
        assert row["purpose"] == "growth"
        assert row["sector"] == "投資信託"


def test_merge_treats_blank_account_as_specific():
    """既存行の account が空欄でも、取込行の specific と同じ行として扱う。

    別扱いにすると、取込のたびに同じ保有が二重に増える。
    """
    existing = [{
        "ticker": "1605", "name": "INPEX", "asset_class": "jp_dividend", "shares": "10",
        "cost_per_share": "1500", "source": "rakuten", "account": "",
    }]
    imported = [{"ticker": "1605", "name": "INPEX", "shares": 20.0, "cost_per_share": 1600.0}]
    merged = dataio.merge_holdings(existing, imported, source="rakuten")
    assert len(merged) == 1
    assert merged[0]["shares"] == 20.0
    assert merged[0]["account"] == "specific"


def test_account_column_is_in_canonical_order():
    assert "account" in dataio.HOLDINGS_COLUMNS
    assert dataio.normalize_account(None) == "specific"
    assert dataio.normalize_account("nan") == "specific"
    assert dataio.normalize_account(" nisa_old ") == "nisa_old"
