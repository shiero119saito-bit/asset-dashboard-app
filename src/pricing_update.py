"""保有行への時価・配当の反映（純関数・外部依存なし）。

取得元（yfinance / 投資信託協会）と保存先（GitHub / ローカルCSV）から切り離した、
「取れた値を行に書き込む」「行から読み出す」ルールだけを持つモジュール。

同じルールを次の4経路から呼ぶため、I/O を持たせない：
- `scripts/refresh_prices.py`（PC・GitHub Actions から実行）
- `scripts/import_holdings.py` / `scripts/record_snapshot.py`
- `src/app.py`（画面は保存された値を読むだけ＝yfinance を呼ばない）
- テスト（price_map / div_map をスタブ注入するだけで検証できる）
"""
from __future__ import annotations

from datetime import date

PRICE_COLUMN = "price"
ASOF_COLUMN = "price_asof"

# 配当。div_per_share は手入力の上書き値で、自動取得は div_annual に書く。
# 分けているのは、定期取得（週1）が手で直した値を潰さないようにするため
MANUAL_DIVIDEND_COLUMN = "div_per_share"
DIVIDEND_COLUMN = "div_annual"
MONTHS_COLUMN = "div_months"
DIVIDEND_ASOF_COLUMN = "div_asof"

MONTHS_SEPARATOR = ";"

# 時価総額（円建て）。配当と同じ週1ジョブが書く＝鮮度は div_asof が表す
MARKET_CAP_COLUMN = "market_cap"


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


def _cell(row: dict, column: str) -> str:
    """行の値を文字列で取り出す。pandas の NaN は空文字にする。"""
    value = row.get(column)
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def format_months(months: list[int]) -> str:
    """権利確定月をCSVセル用の文字列にする（例 [9, 3] → "3;9"）。

    昇順・重複排除で書く＝取得順の揺れで無意味な差分（＝無意味なコミット）を作らない。
    """
    valid = sorted({int(m) for m in months if 1 <= int(m) <= 12})
    return MONTHS_SEPARATOR.join(str(m) for m in valid)


def parse_months(text) -> list[int]:
    """`3;9` 形式のセルを [3, 9] に戻す。壊れた値は黙って捨てる（画面を落とさない）。"""
    months: list[int] = []
    for part in str(text or "").split(MONTHS_SEPARATOR):
        part = part.strip()
        if not part.isdigit():
            continue
        month = int(part)
        if 1 <= month <= 12 and month not in months:
            months.append(month)
    return sorted(months)


def apply_dividends(
    rows: list[dict],
    div_map: dict[str, float],
    months_map: dict[str, list[int]],
    today: date,
) -> tuple[list[dict], int]:
    """div_map / months_map の銘柄の div_annual / div_months / div_asof を更新する。

    `apply_prices` と同じ規約：取得できなかった銘柄は既存の値をそのまま残し（古い値でも
    無いよりまし）、入力の rows は書き換えず複製を返す。返り値は (更新後の行, 更新件数)。

    **div_per_share（手入力）には触れない**。画面は div_per_share → div_annual の順に読むため、
    手で入れた値は定期取得に上書きされない。
    """
    asof = today.isoformat()
    updated = 0
    result: list[dict] = []

    for row in rows:
        new_row = dict(row)
        ticker = str(row.get("ticker", "")).strip()
        annual = div_map.get(ticker)
        months = months_map.get(ticker)
        if annual is None and months is None:
            result.append(new_row)
            continue

        changed = False
        if annual:
            text = format_price(annual)
            changed |= _cell(new_row, DIVIDEND_COLUMN) != text
            new_row[DIVIDEND_COLUMN] = text
        if months is not None:
            text = format_months(months)
            changed |= _cell(new_row, MONTHS_COLUMN) != text
            new_row[MONTHS_COLUMN] = text
        # 値が同じでも as-of は進める（いつ確かめたかを残す）。時価と同じく as-of の
        # 前進も更新として数える＝画面の鮮度表示が「先週のまま」に見えなくなる
        changed |= _cell(new_row, DIVIDEND_ASOF_COLUMN) != asof
        new_row[DIVIDEND_ASOF_COLUMN] = asof
        if changed:
            updated += 1
        result.append(new_row)

    return result, updated


def apply_market_caps(
    rows: list[dict], cap_map: dict[str, float], today: date
) -> tuple[list[dict], int]:
    """cap_map にある銘柄の market_cap を更新する。配当と同じ週1ジョブから呼ぶ。

    `apply_dividends` と同じ規約：取得できなかった銘柄（ETF・投信）は既存値を残し、
    入力の rows は書き換えず複製を返す。as-of 列は持たない（同じジョブが書く div_asof が実態）。

    桁が大きいので整数に丸めて書く。企業規模の区分（兆・千億の境目）には十分な精度で、
    小数を残すと差分だけが毎週動いて無意味なコミットを生む。
    """
    updated = 0
    result: list[dict] = []

    for row in rows:
        new_row = dict(row)
        cap = cap_map.get(str(row.get("ticker", "")).strip())
        if cap:
            text = f"{cap:.0f}"
            if _cell(new_row, MARKET_CAP_COLUMN) != text:
                updated += 1
            new_row[MARKET_CAP_COLUMN] = text
        result.append(new_row)

    return result, updated


def dividend_map(rows: list[dict]) -> dict[str, float]:
    """行から年間配当マップを作る。**手入力（div_per_share）が自動取得（div_annual）に勝つ**。

    画面・スナップショット記録の双方がこの1関数から読む＝優先順位が2か所に散らない。
    どちらも空・0以下の銘柄はキーを作らない（配当なし＝集計から外れる）。
    """
    result: dict[str, float] = {}
    for row in rows:
        ticker = str(row.get("ticker", "")).strip()
        if not ticker:
            continue
        for column in (MANUAL_DIVIDEND_COLUMN, DIVIDEND_COLUMN):
            text = _cell(row, column)
            if not text:
                continue
            try:
                value = float(text)
            except ValueError:
                continue
            if value > 0:
                result[ticker] = value
                break
    return result


def dividend_months_map(rows: list[dict]) -> dict[str, list[int]]:
    """行から権利確定月マップを作る。空欄の銘柄はキーを作らない（＝月不明として集計）。"""
    result: dict[str, list[int]] = {}
    for row in rows:
        ticker = str(row.get("ticker", "")).strip()
        months = parse_months(_cell(row, MONTHS_COLUMN))
        if ticker and months:
            result[ticker] = months
    return result


def stale_days(rows: list[dict], today: date, column: str = ASOF_COLUMN) -> int | None:
    """保存されている値が何日前のものかを返す。1件も無ければ None。

    行ごとに日付が違いうる（取得できた銘柄だけ更新されるため）ので、
    最も新しい as-of を基準にする＝「最後に更新を回した日」からの経過日数。
    column を替えると配当（div_asof）の鮮度にも使える。
    """
    latest: date | None = None
    for row in rows:
        text = str(row.get(column, "")).strip()
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
