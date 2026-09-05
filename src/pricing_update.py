"""保有行への時価反映（純関数・外部依存なし）。

時価の取得元（yfinance）と保存先（GitHub / ローカルCSV）から切り離した、
「取れた値を行に書き込む」ルールだけを持つモジュール。

同じルールを次の3経路から呼ぶため、I/O を持たせない：
- `scripts/refresh_prices.py`（PC・GitHub Actions から実行）
- `scripts/import_holdings.py`（証券会社CSV取込と同時に更新）
- テスト（price_map をスタブ注入するだけで検証できる）
"""
from __future__ import annotations

from datetime import date

PRICE_COLUMN = "price"
ASOF_COLUMN = "price_asof"


def format_price(value: float) -> str:
    """時価をCSVセル用の文字列にする。末尾の不要な0を落として桁を膨らませない。"""
    return f"{value:.4f}".rstrip("0").rstrip(".")


def apply_prices(
    rows: list[dict], price_map: dict[str, float], today: date
) -> tuple[list[dict], int]:
    """price_map にある銘柄の price / price_asof を更新した行リストを返す。

    取得できなかった銘柄（投資信託・通信失敗）は既存の値をそのまま残す
    ＝「古い値でも無いよりまし」。時価ゼロで評価額が消える方が害が大きい。

    引数の rows は書き換えず、複製を返す（呼び出し側が更新前後を比較できるようにする）。
    返り値は (更新後の行, 実際に値が書き換わった件数)。
    """
    asof = today.isoformat()
    updated = 0
    result: list[dict] = []

    for row in rows:
        new_row = dict(row)
        price = price_map.get(str(row.get("ticker", "")).strip())
        if price:
            text = format_price(price)
            # 同値なら書き換えたことにしない（無意味なコミットを生まないため）
            if str(new_row.get(PRICE_COLUMN, "")).strip() != text or \
                    str(new_row.get(ASOF_COLUMN, "")).strip() != asof:
                updated += 1
            new_row[PRICE_COLUMN] = text
            new_row[ASOF_COLUMN] = asof
        result.append(new_row)

    return result, updated


def stale_days(rows: list[dict], today: date) -> int | None:
    """保存されている時価が何日前のものかを返す。時価が1件も無ければ None。

    行ごとに日付が違いうる（取得できた銘柄だけ更新されるため）ので、
    最も新しい price_asof を基準にする＝「最後に更新を回した日」からの経過日数。
    """
    latest: date | None = None
    for row in rows:
        text = str(row.get(ASOF_COLUMN, "")).strip()
        if not text or text.lower() == "nan":
            continue
        try:
            parsed = date.fromisoformat(text)
        except ValueError:
            continue  # 手編集で壊れた値は無視する（画面を落とさない）
        if latest is None or parsed > latest:
            latest = parsed
    if latest is None:
        return None
    return max((today - latest).days, 0)
