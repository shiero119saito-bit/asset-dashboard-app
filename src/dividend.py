"""配当分析ロジック（純関数群）。

配当額は変動・外部由来のため Holding に持たせず、`div_map`（{ticker: 年間配当/株}）と
`months_map`（{ticker: [権利確定月]}）を呼び出し側から注入する（portfolio の price_map と同方針）。
これによりテストを認証情報・通信なしで実行できる。
"""
from __future__ import annotations

from portfolio import INDUSTRY_UNCLASSIFIED, Holding

# 税率（税抜配当の算出に使用）
# jp = 国内課税 20.315%（所得税15.315%＋住民税5%）
# us = 米国源泉10% + 残額への国内20.315% の合算 ≒ 28.2835%
#      外国税額控除（確定申告で米国分を取戻し）は考慮しない保守表示。
TAX_RATE = {
    "jp": 0.20315,
    "us": 0.282835,
}

# NISA口座（旧NISA・つみたて投資枠・成長投資枠）の税率。
# **国内課税は非課税だが、米国株の配当は現地で10%源泉徴収される**（NISAでは外国税額控除も
# 使えないため取り戻せない）。ここを0%にすると手取りを過大表示するので分けて持つ。
NISA_TAX_RATE = {
    "jp": 0.0,
    "us": 0.10,
}

# 課税口座として扱う account の値。**空欄は特定口座扱い**（未設定のデータで
# 非課税と誤表示しないための安全側の既定）
TAXABLE_ACCOUNTS = {"", "specific"}

# 月別バケットで権利確定月が不明な配当を入れるキー
UNKNOWN_MONTH = "不明"


def is_taxable(account: str) -> bool:
    """その口座区分が課税対象か。空欄・未知の値は課税（安全側）。"""
    return str(account or "").strip().lower() in TAXABLE_ACCOUNTS


def tax_rate_for(market: str, account: str = "") -> float:
    """market（jp/us）と口座区分に対する配当の税率。"""
    rates = TAX_RATE if is_taxable(account) else NISA_TAX_RATE
    return rates.get(market, rates["jp"])


def after_tax(amount: float, market: str, account: str = "") -> float:
    """税抜配当額。未知 market は jp 税率を適用。account 未指定は特定口座扱い。"""
    return amount * (1.0 - tax_rate_for(market, account))


def annual_dividend(h: Holding, div_map: dict[str, float]) -> float:
    """銘柄の年間配当（税込）= 1株配当 × 株数。div_map 欠損は0。"""
    return float(div_map.get(h.ticker, 0.0)) * h.shares


def holding_dividend(h: Holding, div_map: dict[str, float], pre_tax: bool = True) -> float:
    """銘柄の年間配当。pre_tax=False で税抜（market と口座区分に応じた税率）。

    集計系（total_annual_dividend・dividend_by_month・dividend_by_sector 等）はすべて
    この関数を通るため、ここで口座区分を見れば全体が正しくなる。
    """
    gross = annual_dividend(h, div_map)
    return gross if pre_tax else after_tax(gross, h.market, h.account)


def total_annual_dividend(
    holdings: list[Holding], div_map: dict[str, float], pre_tax: bool = True
) -> float:
    """総年間配当。pre_tax=False で税抜。"""
    return sum(holding_dividend(h, div_map, pre_tax) for h in holdings)


def effective_tax_rate(holdings: list[Holding], div_map: dict[str, float]) -> float:
    """いまの保有構成での配当の実効税率（0.0〜1.0）。配当が無ければ国内税率。

    将来の配当シミュレーションに渡す。NISA の比率が高いほど税率が下がるので、
    一律 20.315% で見積もるより手取りが実態に近づく。
    """
    gross = total_annual_dividend(holdings, div_map, pre_tax=True)
    if gross == 0:
        return TAX_RATE["jp"]
    net = total_annual_dividend(holdings, div_map, pre_tax=False)
    return (gross - net) / gross


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


def dividend_by_industry(
    holdings: list[Holding], div_map: dict[str, float], pre_tax: bool = True
) -> dict[str, float]:
    """業種（東証33業種）別の年間配当。空欄は「未分類」に寄せる。

    どの業種から配当を受け取っているか＝配当の集中度を見るための切り口。
    """
    return _dividend_by_key(
        holdings, div_map, lambda h: h.industry or INDUSTRY_UNCLASSIFIED, pre_tax
    )


def dividend_by_market(
    holdings: list[Holding], div_map: dict[str, float], pre_tax: bool = True
) -> dict[str, float]:
    """日米（market）別の年間配当。"""
    return _dividend_by_key(holdings, div_map, lambda h: h.market, pre_tax)
