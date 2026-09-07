"""事業（副業）・労働収入（パート）の実績台帳。純関数のみ。

55歳以降の生活は「配当＋インデックス取り崩し＋事業＋労働収入」で組む設計のため、
資産の外側にある収入も進捗の対象になる。目標値だけを置くと「月5万を達成できているか」が
測れないので、月次の実績を記録して直近平均と目標比を出す。

金額は円・**手取り**で記録する（配当の目標が税抜なので基準を揃える）。
保存は storage.py（呼び出し側）が行う＝cash.py / dividend_history.py と同じ方針。
"""
from __future__ import annotations

import csv
import io

INCOME_COLUMNS = ("month", "category", "amount", "note")

# 収入の区分。事業＝副業（役務・物販等）、労働収入＝パート等の給与
BUSINESS = "事業"
LABOR = "労働収入"
CATEGORIES = (BUSINESS, LABOR)

# 同じ月・同じ区分は1行にまとめる（月内の細かい入金は合計して入れる）
KEY_COLUMNS = ("month", "category")


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
    """1行を保存形式へ整える。month は YYYY-MM に丸める（日付を入れても月で扱う）。"""
    out = {col: _clean(row.get(col)) for col in INCOME_COLUMNS}
    out["month"] = out["month"][:7]
    out["amount"] = str(round(_to_float(row.get("amount"))))
    return out


def parse_csv(text: str | None) -> list[dict]:
    """収入CSVを行リストへ。月が空の行は捨てる。空・None は空リスト。"""
    if not text or not text.strip():
        return []
    return [normalize(raw) for raw in csv.DictReader(io.StringIO(text))
            if _clean(raw.get("month"))]


def serialize_csv(rows: list[dict]) -> str:
    """行リストをCSV文字列へ（parse_csv の逆）。月→区分の順に並べる。"""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(INCOME_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in sort_rows(rows):
        writer.writerow(normalize(row))
    return out.getvalue()


def sort_rows(rows: list[dict]) -> list[dict]:
    """月→区分の順に並べる。"""
    return sorted(rows, key=lambda r: (_clean(r.get("month")), _clean(r.get("category"))))


def merge(existing: list[dict], imported: list[dict]) -> list[dict]:
    """取込行を既存へマージする。同じ (月, 区分) は取込側で上書き＝二重計上しない。"""
    table = {tuple(_clean(r.get(c)) for c in KEY_COLUMNS): normalize(r) for r in existing}
    for row in imported:
        table[tuple(_clean(row.get(c)) for c in KEY_COLUMNS)] = normalize(row)
    return sort_rows(list(table.values()))


def months(rows: list[dict]) -> list[str]:
    """記録のある月（昇順）。"""
    return sorted({_clean(r.get("month")) for r in rows if _clean(r.get("month"))})


def by_month(rows: list[dict], category: str | None = None) -> dict[str, float]:
    """月別の合計。category を渡すとその区分だけ。"""
    out: dict[str, float] = {}
    for row in rows:
        if category and _clean(row.get("category")) != category:
            continue
        key = _clean(row.get("month"))
        if not key:
            continue
        out[key] = out.get(key, 0.0) + _to_float(row.get("amount"))
    return out


def by_category(rows: list[dict], month: str | None = None) -> dict[str, float]:
    """区分別の合計。month を渡すとその月だけ。"""
    out: dict[str, float] = {category: 0.0 for category in CATEGORIES}
    for row in rows:
        if month and _clean(row.get("month")) != month:
            continue
        key = _clean(row.get("category")) or BUSINESS
        out[key] = out.get(key, 0.0) + _to_float(row.get("amount"))
    return out


def recent_average(rows: list[dict], category: str | None = None, span: int = 12) -> float:
    """直近 span か月の月平均。**記録が無い月は0として数える**。

    「稼働した月だけの平均」にすると、月5万という目標に対して実力を過大評価する。
    記録が1件も無ければ0。
    """
    totals = by_month(rows, category)
    if not totals:
        return 0.0
    latest = max(totals)
    recent = sorted(m for m in totals if m <= latest)[-span:]
    span_used = min(span, len(_month_range(recent[0], latest)))
    return sum(totals[m] for m in recent) / max(1, span_used)


def _month_range(start: str, end: str) -> list[str]:
    """start〜end の月を並べる（YYYY-MM）。不正な入力なら [end] を返す。"""
    try:
        sy, sm = (int(part) for part in start.split("-")[:2])
        ey, em = (int(part) for part in end.split("-")[:2])
    except (ValueError, IndexError):
        return [end]
    out = []
    year, month = sy, sm
    while (year, month) <= (ey, em) and len(out) < 600:
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out or [end]


def progress(actual_monthly: float, goal_monthly: float) -> float:
    """月額目標に対する達成率（%）。目標0以下なら0。"""
    return actual_monthly / goal_monthly * 100.0 if goal_monthly > 0 else 0.0
