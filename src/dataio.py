"""保有データ・個人設定の入出力ヘルパー（純関数）。

CSV文字列 → rows（list[dict]）の変換、設定JSONの相互変換を担う。
secrets / アップロード / ローカルファイルいずれのソースでも同じパーサを再利用できるよう、
ファイルI/O から分離した純関数として切り出す（読み書きは app.py 側）。
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date

import pandas as pd

# holdings の必須列（最低限これが無いと集計できない）
REQUIRED_COLUMNS = ("ticker", "name", "asset_class", "shares", "cost_per_share")

# holdings.csv の正準列順。CLI（importers.merge）と画面編集の保存で同一形式にする。
# 取込側の importers に依存させないのは、公開デプロイにはダッシュボードに必要な
# モジュールしか置かないため（importers は証券会社CSV取込＝ローカル専用）。
HOLDINGS_COLUMNS = (
    "ticker",
    "name",
    "asset_class",
    "shares",
    "cost_per_share",
    "sector",
    "market",
    "div_per_share",
    "purpose",
    "source",
    "price",
    "price_asof",
)


def parse_holdings_csv(text: str) -> list[dict]:
    """CSV文字列を rows（list[dict]）へ変換する。

    追加列（sector / market / div_per_share）は任意。欠損は呼び出し側（build_holdings）が補完。
    必須列が欠けている場合は ValueError。空入力は空リスト。
    """
    if text is None or text.strip() == "":
        return []
    df = pd.read_csv(io.StringIO(text))
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"必須列が不足しています: {', '.join(missing)}")
    return df.to_dict("records")


def serialize_holdings_csv(rows: list[dict], columns: tuple[str, ...] | None = None) -> str:
    """rows を holdings.csv 形式の文字列にする（parse_holdings_csv の逆）。

    列順は HOLDINGS_COLUMNS に合わせ、CLI が書くファイルと同一形式にする。
    欠損値・NaN は空文字にして「nan」という文字列がCSVに残らないようにする
    （pandas が空欄を float('nan') で読むため、素直に str() すると "nan" になる）。
    """
    cols = columns or HOLDINGS_COLUMNS
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(cols), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({col: _cell(row.get(col)) for col in cols})
    return buf.getvalue()


def _cell(value) -> str:
    """CSVセル1つ分の文字列化。None・NaN・"nan" は空文字に落とす。"""
    if value is None:
        return ""
    if isinstance(value, float) and value != value:  # NaN
        return ""
    s = str(value).strip()
    return "" if s.lower() == "nan" else s


# --- 取込データのマージ（証券会社CSV取込・画面アップロードの両方で使う）---
# 同一銘柄を複数の証券会社で保有しうるため、キーは ticker 単体ではなく (ticker, source)。
# 既存の分類済みメタ（asset_class/sector/market/purpose/div_per_share）は上書きせず、
# 数量・取得単価・名称だけを最新値で更新する。

REIT_NAME_MARKERS = ("ＲＥＩＴ", "REIT", "リート")


def _default_metadata(name: str, hint: dict | None = None) -> dict:
    """新規銘柄の既定分類。名称にREIT系の表記があれば reit、なければ個別株jp_dividendとする。

    hint（=importerが返した行dict自体）に asset_class/sector/market があれば既定値を
    上書きする（投資信託等、importer側で分類済みのケース向け）。既存のrakuten/sbi/esmart
    importerはこれらのキーを含まないため、hintを渡さない呼び出しは従来どおりの挙動になる。

    優待/高配当の別（purpose）は保有動機の主観情報のため対象外＝常に空欄。
    """
    if any(marker in name for marker in REIT_NAME_MARKERS):
        meta = {"asset_class": "reit", "sector": "REIT", "market": "jp", "div_per_share": "", "purpose": ""}
    else:
        meta = {"asset_class": "jp_dividend", "sector": "個別株", "market": "jp", "div_per_share": "", "purpose": ""}
    if hint:
        for key in ("asset_class", "sector", "market"):
            if hint.get(key):
                meta[key] = hint[key]
    return meta


def _key(ticker: str, source: str) -> tuple[str, str]:
    return (str(ticker).strip(), str(source).strip())


def merge_holdings(existing_rows: list[dict], imported_rows: list[dict], source: str) -> list[dict]:
    """既存 holdings 行 + 証券会社CSV取込行 → マージ済み holdings 行リスト。

    existing_rows: holdings.csv 由来の行（(ticker, source) をキーに分類済みメタを保持）
    imported_rows: importers.rakuten/sbi の parse_*_csv が返す
                    {"ticker", "name", "shares", "cost_per_share"} のリスト
    source: 今回の取込元（例 "rakuten"/"sbi"）。同一 (ticker, source) のみ更新対象にする。
    """
    by_key: dict[tuple[str, str], dict] = {
        _key(row.get("ticker"), row.get("source", "")): dict(row) for row in existing_rows
    }

    for imported in imported_rows:
        ticker = str(imported["ticker"]).strip()
        k = _key(ticker, source)
        if k in by_key:
            merged = by_key[k]
            merged["name"] = imported["name"]
            merged["shares"] = imported["shares"]
            merged["cost_per_share"] = imported["cost_per_share"]
        else:
            merged = {
                "ticker": ticker,
                "name": imported["name"],
                "shares": imported["shares"],
                "cost_per_share": imported["cost_per_share"],
                "source": source,
                **_default_metadata(imported["name"], imported),
            }
        by_key[k] = merged

    return [
        {col: row.get(col, "") for col in HOLDINGS_COLUMNS}
        for row in by_key.values()
    ]


# --- 個人設定（生年月日）。機微情報のため保存先は .gitignore 済み ---

BIRTH_DATE_KEY = "birth_date"


def parse_birth_date(text: str | None) -> date | None:
    """設定JSON文字列から生年月日を取り出す。未設定・壊れていれば None。

    設定ファイルは手で壊れうる（手編集・空ファイル）ため、例外を投げず
    None を返して「未設定」として扱う（呼び出し側は入力を促すだけで済む）。
    """
    if not text or not text.strip():
        return None
    try:
        value = json.loads(text).get(BIRTH_DATE_KEY)
        return date.fromisoformat(value) if value else None
    except (ValueError, TypeError, AttributeError):
        return None


def serialize_birth_date(birth: date) -> str:
    """生年月日を設定JSON文字列にする（ISO 8601）。"""
    return json.dumps({BIRTH_DATE_KEY: birth.isoformat()}, ensure_ascii=False, indent=2)
