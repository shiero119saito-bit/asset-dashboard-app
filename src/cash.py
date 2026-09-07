"""現金・預金（待機資金）の純関数群。

アプリは証券口座の中だけを見ていたため、**総資産・現金比率・運用比率が出せなかった**。
残高を手入力で数行持てば、この3つと「総資産◯万円」の達成率がすべて成立する。
入出金の履歴は持たない（それが要るなら別途台帳）。ここにあるのは「いまいくらあるか」だけ。

金額は円。外部依存は持たず、保存は storage.py（呼び出し側）が行う。
"""
from __future__ import annotations

import csv
import io

CASH_COLUMNS = ("name", "amount", "note")


def _to_float(value) -> float:
    try:
        text = str(value).strip().replace(",", "").replace("¥", "")
        return float(text) if text else 0.0
    except (TypeError, ValueError):
        return 0.0


def _clean(value) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() == "nan" else text


def normalize(row: dict) -> dict:
    """1行を保存形式へ整える。未知の列は落とし、不足列は空で補う。"""
    out = {col: _clean(row.get(col)) for col in CASH_COLUMNS}
    out["amount"] = str(round(_to_float(row.get("amount"))))
    return out


def parse_csv(text: str | None) -> list[dict]:
    """現金CSVを行リストへ。名前が空の行は捨てる（編集表の空行対策）。"""
    if not text or not text.strip():
        return []
    return [
        normalize(raw) for raw in csv.DictReader(io.StringIO(text))
        if _clean(raw.get("name"))
    ]


def serialize_csv(rows: list[dict]) -> str:
    """行リストをCSV文字列へ（parse_csv の逆）。入力順を保つ。"""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(CASH_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        if _clean(row.get("name")):
            writer.writerow(normalize(row))
    return out.getvalue()


def total(rows: list[dict]) -> float:
    """現金の合計。名前が空の行は数えない。"""
    return sum(_to_float(r.get("amount")) for r in rows if _clean(r.get("name")))


def net_worth(market_value: float, cash_rows: list[dict]) -> float:
    """総資産＝運用資産（評価額）＋現金。"""
    return market_value + total(cash_rows)


def cash_ratio(market_value: float, cash_rows: list[dict]) -> float:
    """総資産に占める現金の割合（%）。総資産0なら0。"""
    assets = net_worth(market_value, cash_rows)
    return total(cash_rows) / assets * 100.0 if assets else 0.0


def invested_ratio(market_value: float, cash_rows: list[dict]) -> float:
    """総資産に占める運用資産の割合（%）。総資産0なら0。"""
    assets = net_worth(market_value, cash_rows)
    return market_value / assets * 100.0 if assets else 0.0
