"""資産スナップショット（月次の実績記録）の純関数群。

いまのアプリは「現在の評価額」しか持たず、時間軸の実測が残らない。1か月に1行を
積み上げて推移を見られるようにする。外部依存（I/O・通信）は持たず、保存は
storage.py（呼び出し側）が行う＝portfolio.py / dividend.py と同方針。

**同じ月は上書きする**（月内に何度記録しても1行）。月次の点だけを持ち、
欠損月は補間しない＝記録が無い月は線を引かない（実測でない値を混ぜないため）。
"""
from __future__ import annotations

import csv
import io
from datetime import date

import dividend as dv
import portfolio as pf

# 保存する列。増やすときは末尾に足す（既存CSVを読めなくしないため）
SNAPSHOT_COLUMNS = (
    "date",                        # 記録日（YYYY-MM-DD）。月キーは先頭7文字
    "total_cost",                  # 投下元本
    "total_market",                # 評価額
    "gain",                        # 含み損益
    "annual_dividend_pre_tax",     # 年間配当見込み（税込）
    "annual_dividend_after_tax",   # 年間配当見込み（税抜＝口座区分を反映）
    "index_pct",
    "us_dividend_pct",
    "jp_dividend_pct",
    "reit_pct",
)

# 構成比の列と資産クラスの対応
_ALLOCATION_COLUMNS = {
    "index": "index_pct",
    "us_dividend": "us_dividend_pct",
    "jp_dividend": "jp_dividend_pct",
    "reit": "reit_pct",
}


def month_key(value: str) -> str:
    """日付文字列から月キー（YYYY-MM）を取り出す。空・不正はそのまま返す。"""
    text = str(value or "").strip()
    return text[:7] if len(text) >= 7 else text


def build_record(
    holdings: list[pf.Holding],
    div_map: dict[str, float],
    on: date | None = None,
) -> dict:
    """いまの保有から1行分のスナップショットを作る。

    配当は税込・税抜の両方を持つ。税抜は口座区分（NISA）を反映した実額に近い値で、
    「目標の月6〜10万」に対して見るべきはこちら。
    """
    on = on or date.today()
    allocation = pf.allocation_by_class(holdings)
    record = {
        "date": on.isoformat(),
        "total_cost": round(pf.total_cost(holdings)),
        "total_market": round(pf.total_market(holdings)),
        "gain": round(pf.total_gain(holdings)),
        "annual_dividend_pre_tax": round(
            dv.total_annual_dividend(holdings, div_map, pre_tax=True)
        ),
        "annual_dividend_after_tax": round(
            dv.total_annual_dividend(holdings, div_map, pre_tax=False)
        ),
    }
    for asset_class, column in _ALLOCATION_COLUMNS.items():
        record[column] = round(allocation.get(asset_class, 0.0), 2)
    return record


def parse_csv(text: str | None) -> list[dict]:
    """スナップショットCSVを行リストへ。空・None は空リスト。

    知らない列は捨てず読み飛ばす（将来列を足しても古い版のコードが壊れないように）。
    """
    if not text or not text.strip():
        return []
    rows = []
    for raw in csv.DictReader(io.StringIO(text)):
        row = {col: raw.get(col, "") for col in SNAPSHOT_COLUMNS}
        if str(row["date"]).strip():
            rows.append(row)
    return rows


def serialize_csv(rows: list[dict]) -> str:
    """行リストをCSV文字列へ（parse_csv の逆）。日付の昇順で書く。"""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(SNAPSHOT_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in sorted(rows, key=lambda r: str(r.get("date", ""))):
        writer.writerow({col: row.get(col, "") for col in SNAPSHOT_COLUMNS})
    return out.getvalue()


def upsert(rows: list[dict], record: dict) -> list[dict]:
    """同じ月の行を置き換える（無ければ追加）。日付の昇順で返す。

    月内に何度実行しても行が増えないので、cron を月初に置いても手で押しても同じ結果になる。
    """
    key = month_key(record.get("date", ""))
    kept = [r for r in rows if month_key(r.get("date", "")) != key]
    kept.append(dict(record))
    return sorted(kept, key=lambda r: str(r.get("date", "")))


def _to_float(value) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def series(rows: list[dict], column: str) -> tuple[list[str], list[float]]:
    """推移グラフ用に (日付, 値) の並びを返す。日付昇順。数値化できない値は0。"""
    ordered = sorted(rows, key=lambda r: str(r.get("date", "")))
    return (
        [str(r.get("date", "")) for r in ordered],
        [_to_float(r.get(column)) for r in ordered],
    )


def latest(rows: list[dict]) -> dict | None:
    """最新の1行。無ければ None。"""
    if not rows:
        return None
    return sorted(rows, key=lambda r: str(r.get("date", "")))[-1]


def change_from_previous(rows: list[dict], column: str) -> tuple[float, float] | None:
    """直近1件とその1つ前の差（絶対値, 変化率%）。比較対象が無ければ None。

    KPIカードの「前月比」に使う。前の値が0のときは率を0%とする（無限大を出さない）。
    """
    ordered = sorted(rows, key=lambda r: str(r.get("date", "")))
    if len(ordered) < 2:
        return None
    current = _to_float(ordered[-1].get(column))
    previous = _to_float(ordered[-2].get(column))
    delta = current - previous
    rate = (delta / previous * 100.0) if previous else 0.0
    return (delta, rate)
