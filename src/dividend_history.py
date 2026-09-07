"""受取配当の実績（履歴）の純関数群。

`dividend.py` が扱うのは **予定**（1株配当 × 株数）で、実際にいくら受け取ったかは
どこにも残っていなかった。「55歳で月6〜10万」という目標に対する進捗は実績でしか測れず、
増配率もここからしか出せない。外部依存は持たず、保存は storage.py（呼び出し側）が行う。

金額はすべて**円**で持つ。外貨建ての配当は受取時の円換算額を入れる（為替を後から
再計算しない＝受け取った事実をそのまま記録する）。米国株の現地源泉10%は tax に含める。
"""
from __future__ import annotations

import csv
import io

# 保存する列。増やすときは末尾に足す（既存CSVを読めなくしないため）
HISTORY_COLUMNS = (
    "date",      # 受取日（YYYY-MM-DD）
    "ticker",
    "name",
    "gross",     # 税引前（円）
    "tax",       # 税額（円）。NISA は 0。米国株は現地源泉を含む
    "net",       # 手取り（円）。空なら gross - tax で補う
    "account",   # specific / nisa_old / nisa_tsumitate / nisa_growth
    "source",    # 証券会社
    "note",
)

# 同じ受取を二重計上しないためのキー。1日に同一銘柄・同一口座で2回入ることは無い
KEY_COLUMNS = ("date", "ticker", "account")


def _to_float(value) -> float:
    try:
        text = str(value).strip().replace(",", "").replace("¥", "")
        return float(text) if text else 0.0
    except (TypeError, ValueError):
        return 0.0


def _clean(value) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() == "nan" else text


def net_amount(row: dict) -> float:
    """手取り。net が空なら gross - tax で補う（入力を楽にするため）。"""
    if _clean(row.get("net")):
        return _to_float(row.get("net"))
    return _to_float(row.get("gross")) - _to_float(row.get("tax"))


def normalize(row: dict) -> dict:
    """1行を保存形式へ整える。未知の列は落とし、不足列は空で補う。"""
    out = {col: _clean(row.get(col)) for col in HISTORY_COLUMNS}
    out["net"] = str(round(net_amount(row)))
    return out


def parse_csv(text: str | None) -> list[dict]:
    """履歴CSVを行リストへ。日付が空の行は捨てる。空・None は空リスト。"""
    if not text or not text.strip():
        return []
    return [
        normalize(raw) for raw in csv.DictReader(io.StringIO(text))
        if _clean(raw.get("date"))
    ]


def serialize_csv(rows: list[dict]) -> str:
    """行リストをCSV文字列へ（parse_csv の逆）。受取日の昇順で書く。"""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(HISTORY_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in sort_rows(rows):
        writer.writerow(normalize(row))
    return out.getvalue()


def sort_rows(rows: list[dict]) -> list[dict]:
    """受取日→銘柄の順に並べる。"""
    return sorted(rows, key=lambda r: (_clean(r.get("date")), _clean(r.get("ticker"))))


def _key(row: dict) -> tuple:
    return tuple(_clean(row.get(col)) for col in KEY_COLUMNS)


def merge(existing: list[dict], imported: list[dict]) -> list[dict]:
    """取込行を既存へマージする。同じ (受取日, 銘柄, 口座) は取込側で上書き。

    同じCSVを2回取り込んでも二重計上にならない＝取込をためらわずに済む。
    """
    table = {_key(row): normalize(row) for row in existing}
    for row in imported:
        table[_key(row)] = normalize(row)
    return sort_rows(list(table.values()))


def _sum_by(rows: list[dict], key_fn, pre_tax: bool) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        amount = _to_float(row.get("gross")) if pre_tax else net_amount(row)
        key = key_fn(row)
        out[key] = out.get(key, 0.0) + amount
    return out


def by_year(rows: list[dict], pre_tax: bool = False) -> dict[str, float]:
    """年別の受取額。既定は手取り（目標の月6〜10万は手取りで見るため）。"""
    return _sum_by(rows, lambda r: _clean(r.get("date"))[:4], pre_tax)


def by_month(rows: list[dict], year: str | None = None, pre_tax: bool = False) -> dict[str, float]:
    """月別（YYYY-MM）の受取額。year を渡すとその年だけに絞る。"""
    target = [r for r in rows if not year or _clean(r.get("date")).startswith(str(year))]
    return _sum_by(target, lambda r: _clean(r.get("date"))[:7], pre_tax)


def by_ticker(rows: list[dict], year: str | None = None, pre_tax: bool = False) -> dict[str, float]:
    """銘柄別の受取額。"""
    target = [r for r in rows if not year or _clean(r.get("date")).startswith(str(year))]
    return _sum_by(target, lambda r: _clean(r.get("ticker")), pre_tax)


def by_industry(
    rows: list[dict],
    industry_by_ticker: dict[str, str],
    year: str | None = None,
    pre_tax: bool = False,
    unclassified: str = "未分類",
) -> dict[str, float]:
    """業種別の受取額。業種は保有データ（holdings の industry 列）から引く。

    既に売った銘柄は保有に無いため未分類に落ちる。実績は残るので消さない。
    """
    target = [r for r in rows if not year or _clean(r.get("date")).startswith(str(year))]
    return _sum_by(
        target,
        lambda r: industry_by_ticker.get(_clean(r.get("ticker"))) or unclassified,
        pre_tax,
    )


def years(rows: list[dict]) -> list[str]:
    """記録のある年の一覧（昇順）。"""
    return sorted({_clean(r.get("date"))[:4] for r in rows if _clean(r.get("date"))})


def growth_rate(rows: list[dict], pre_tax: bool = False) -> float | None:
    """受取実績からの年平均増配率（%）。

    最初と最後の年の受取額から年平均成長率（CAGR）を出す。**部分的な年（今年）を
    含めると過小評価になる**ため、呼び出し側で当年を除いてから渡すこと。
    比較できる年が2つ未満、または起点が0なら None。
    """
    totals = by_year(rows, pre_tax)
    labels = sorted(totals)
    if len(labels) < 2:
        return None
    first, last = totals[labels[0]], totals[labels[-1]]
    span = int(labels[-1]) - int(labels[0])
    if first <= 0 or span <= 0:
        return None
    return ((last / first) ** (1.0 / span) - 1.0) * 100.0


def progress_against_plan(rows: list[dict], planned_annual: float, year: str) -> float | None:
    """当年の受取実績（手取り）が予定額の何%かを返す。予定が0なら None。

    予定（保有 × 1株配当）に対して実際どれだけ入ったかを見る。減配・売却・
    権利落ち後の買い増しなどのズレがここに出る。
    **planned_annual は税抜（手取りベース）を渡すこと**＝実績と税基準を揃えないと
    2割ずれた到達率になる。
    """
    if planned_annual <= 0:
        return None
    received = by_year(rows, pre_tax=False).get(str(year), 0.0)
    return received / planned_annual * 100.0
