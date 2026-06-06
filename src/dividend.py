"""配当分析ロジック（純関数群）。

配当額は変動・外部由来のため Holding に持たせず、`div_map`（{ticker: 年間配当/株}）と
`months_map`（{ticker: [権利確定月]}）を呼び出し側から注入する（portfolio の price_map と同方針）。
これによりテストを認証情報・通信なしで実行できる。
"""
from __future__ import annotations

from portfolio import Holding

# 税率（税抜配当の算出に使用）
# jp = 国内課税 20.315%（所得税15.315%＋住民税5%）
# us = 米国源泉10% + 残額への国内20.315% の合算 ≒ 28.2835%
#      外国税額控除（確定申告で米国分を取戻し）は考慮しない保守表示。
TAX_RATE = {
    "jp": 0.20315,
    "us": 0.282835,
}

# 月別バケットで権利確定月が不明な配当を入れるキー
UNKNOWN_MONTH = "不明"


def after_tax(amount: float, market: str) -> float:
    """税抜配当額。未知 market は jp 税率を適用。"""
    rate = TAX_RATE.get(market, TAX_RATE["jp"])
    return amount * (1.0 - rate)


def annual_dividend(h: Holding, div_map: dict[str, float]) -> float:
    """銘柄の年間配当（税込）= 1株配当 × 株数。div_map 欠損は0。"""
    return float(div_map.get(h.ticker, 0.0)) * h.shares


def holding_dividend(h: Holding, div_map: dict[str, float], pre_tax: bool = True) -> float:
    """銘柄の年間配当。pre_tax=False で税抜（market 別税率）。"""
    gross = annual_dividend(h, div_map)
    return gross if pre_tax else after_tax(gross, h.market)


def total_annual_dividend(
    holdings: list[Holding], div_map: dict[str, float], pre_tax: bool = True
) -> float:
    """総年間配当。pre_tax=False で税抜。"""
    return sum(holding_dividend(h, div_map, pre_tax) for h in holdings)


def yield_on_cost(holdings: list[Holding], div_map: dict[str, float]) -> float:
    """取得額ベース配当利回り（%・税込）。取得額0なら0。"""
    cost = sum(h.cost_value for h in holdings)
    if cost == 0:
        return 0.0
    return total_annual_dividend(holdings, div_map, pre_tax=True) / cost * 100.0


def yield_on_market(holdings: list[Holding], div_map: dict[str, float]) -> float:
    """評価額ベース配当利回り（%・税込）。評価額0なら0。"""
    market = sum(h.market_value for h in holdings)
    if market == 0:
        return 0.0
    return total_annual_dividend(holdings, div_map, pre_tax=True) / market * 100.0


def dividend_by_month(
    holdings: list[Holding],
    div_map: dict[str, float],
    months_map: dict[str, list[int]],
    pre_tax: bool = True,
) -> dict:
    """権利確定月別の配当。複数月の銘柄は年間配当を均等配分。

    返り値は 1〜12 の各月キー（float）＋ 月不明分の `UNKNOWN_MONTH` キー。
    月不明（months_map に無い/空）の配当は UNKNOWN_MONTH に集約する。
    """
    result: dict = {m: 0.0 for m in range(1, 13)}
    result[UNKNOWN_MONTH] = 0.0
    for h in holdings:
        total = holding_dividend(h, div_map, pre_tax)
        if total == 0:
            continue
        months = [m for m in months_map.get(h.ticker, []) if 1 <= int(m) <= 12]
        if not months:
            result[UNKNOWN_MONTH] += total
            continue
        per = total / len(months)
        for m in months:
            result[int(m)] += per
    return result


def _dividend_by_key(
    holdings: list[Holding],
    div_map: dict[str, float],
    key_fn,
    pre_tax: bool,
) -> dict[str, float]:
    out: dict[str, float] = {}
    for h in holdings:
        amount = holding_dividend(h, div_map, pre_tax)
        key = key_fn(h)
        out[key] = out.get(key, 0.0) + amount
    return out


def dividend_by_sector(
    holdings: list[Holding], div_map: dict[str, float], pre_tax: bool = True
) -> dict[str, float]:
    """セクター別の年間配当。"""
    return _dividend_by_key(holdings, div_map, lambda h: h.sector, pre_tax)


def dividend_by_market(
    holdings: list[Holding], div_map: dict[str, float], pre_tax: bool = True
) -> dict[str, float]:
    """日米（market）別の年間配当。"""
    return _dividend_by_key(holdings, div_map, lambda h: h.market, pre_tax)
