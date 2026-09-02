"""保有データ・個人設定の入出力ヘルパー（純関数）。

CSV文字列 → rows（list[dict]）の変換、設定JSONの相互変換を担う。
secrets / アップロード / ローカルファイルいずれのソースでも同じパーサを再利用できるよう、
ファイルI/O から分離した純関数として切り出す（読み書きは app.py 側）。
"""
from __future__ import annotations

import io
import json
from datetime import date

import pandas as pd

# holdings の必須列（最低限これが無いと集計できない）
REQUIRED_COLUMNS = ("ticker", "name", "asset_class", "shares", "cost_per_share")


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
