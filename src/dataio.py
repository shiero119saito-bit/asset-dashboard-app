"""保有データの入出力ヘルパー（純関数）。

CSV文字列 → rows（list[dict]）の変換を担う。secrets / アップロード / ローカルファイル
いずれのソースでも同じパーサを再利用できるよう、I/O から分離した純関数として切り出す。
"""
from __future__ import annotations

import io

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
