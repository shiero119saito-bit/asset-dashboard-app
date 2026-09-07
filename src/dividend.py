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


def yield_at_purchase(holdings: list[Holding]) -> float | None:
    """購入時利回り（%・額面）。購入時1株配当が入っている保有だけで加重平均する。

    新規投資の効率を測る指標。増配で動く簿価利回り（yield_on_cost）とは別物で、
    「いくらの利回りで買えているか」を見る。**未入力の銘柄は母数にも入れない**
    ＝入力済みが1件も無ければ None（画面は「—」を出す）。
    """
    cost = sum(h.cost_value for h in holdings if h.div_at_purchase > 0)
    if cost <= 0:
        return None
    annual = sum(h.div_at_purchase * h.shares for h in holdings if h.div_at_purchase > 0)
    return annual / cost * 100.0


def shortfall(target_annual: float, current_annual: float) -> float:
    """目標に対する不足配当額。既に上回っていれば0（マイナスを返さない）。"""
    return max(0.0, target_annual - current_annual)


def required_investment(
    shortfall_after_tax: float, purchase_yield_pct: float, tax_rate: float
) -> float:
    """不足配当を埋めるのに要る追加投資額。

    **目標も不足も税抜（手取り）で扱う**ため、額面利回りのまま割ってはいけない。
    手取り利回り＝想定購入時利回り ×(1−実効税率) で割る（税率20.315%なら約1.25倍の金額が要る）。

    追加投資した分の将来増配は織り込まない＝**保守側（多めに要求）**。
    利回りが0以下、または不足が0なら0を返す。
    """
    net_yield = purchase_yield_pct / 100.0 * (1.0 - tax_rate)
    if shortfall_after_tax <= 0 or net_yield <= 0:
        return 0.0
    return shortfall_after_tax / net_yield


def project_dividend(current_annual: float, growth_pct: float, years: int) -> float:
    """増配だけで到達する将来の年間配当（追加投資なし）。years が0以下なら現在値。"""
    if years <= 0:
        return current_annual
    return current_annual * (1.0 + growth_pct / 100.0) ** years


def growth_scenarios(
    current_annual: float,
    target_annual: float,
    years: int,
    purchase_yield_pct: float,
    tax_rate: float,
    growth_rates: tuple[float, ...] = (0.0, 3.0, 5.0),
) -> list[dict]:
    """増配シナリオ別の「将来配当」と「必要追加投資額」。

    増配率が高いほど必要投資額は小さくなる。その構造を見せるためのものだが、
    **増配は保証されない**ので保守（0%）を基準に読むこと。
    """
    return [
        {
            "growth": rate,
            "projected": project_dividend(current_annual, rate, years),
            "shortfall": shortfall(target_annual, project_dividend(current_annual, rate, years)),
            "required": required_investment(
                shortfall(target_annual, project_dividend(current_annual, rate, years)),
                purchase_yield_pct, tax_rate,
            ),
        }
        for rate in growth_rates
    ]


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
