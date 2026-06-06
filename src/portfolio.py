"""ポートフォリオ集計ロジック（純関数群）。

外部依存（株価取得・I/O）を一切持たない。時価は呼び出し側から price_map で注入する。
これによりテストを認証情報・ネットワークなしで実行できる（CLAUDE.md スタブ化方針）。
"""
from __future__ import annotations

from dataclasses import dataclass

# 資産クラス区分（holdings.csv の asset_class 列と対応）
ASSET_CLASSES = ("index", "us_dividend", "jp_dividend", "reit")

# 資産クラスの日本語表示名
ASSET_CLASS_LABELS = {
    "index": "インデックス",
    "us_dividend": "米国高配当",
    "jp_dividend": "日本高配当",
    "reit": "REIT",
}

# 目標アセットアロケーション（%）— personal_asset_strategy より
TARGET_ALLOCATION = {
    "index": 60.0,
    "us_dividend": 20.0,
    "jp_dividend": 15.0,
    "reit": 5.0,
}


@dataclass(frozen=True)
class Holding:
    """保有1銘柄。cost_per_share・shares は取得時情報、price は現在値。

    sector・market は配当/集計の切り口に使う静的メタ（CSV由来）。
    変動する配当額は Holding に持たせず、dividend モジュールで div_map を注入する。
    """

    ticker: str
    name: str
    asset_class: str
    shares: float
    cost_per_share: float
    price: float
    sector: str = "その他"
    market: str = "us"

    @property
    def cost_value(self) -> float:
        """取得額（取得単価 × 株数）。"""
        return self.cost_per_share * self.shares

    @property
    def market_value(self) -> float:
        """評価額（現在値 × 株数）。"""
        return self.price * self.shares

    @property
    def gain(self) -> float:
        """含み損益（評価額 − 取得額）。"""
        return self.market_value - self.cost_value

    @property
    def gain_rate(self) -> float:
        """含み損益率（%）。取得額0なら0。"""
        if self.cost_value == 0:
            return 0.0
        return self.gain / self.cost_value * 100.0


def build_holdings(rows: list[dict], price_map: dict[str, float]) -> list[Holding]:
    """holdings.csv 由来の行 + 時価マップから Holding リストを生成する。

    rows: ticker, name, asset_class, shares, cost_per_share を持つ dict のリスト
    price_map: {ticker: 現在値}。欠損時は取得単価で代替（評価額=取得額になる）
    """
    holdings: list[Holding] = []
    for row in rows:
        ticker = str(row["ticker"]).strip()
        asset_class = str(row["asset_class"]).strip()
        if asset_class not in ASSET_CLASSES:
            raise ValueError(
                f"未知の asset_class '{asset_class}'（{ticker}）。"
                f"許容値: {', '.join(ASSET_CLASSES)}"
            )
        shares = float(row["shares"])
        cost_per_share = float(row["cost_per_share"])
        price = float(price_map.get(ticker, cost_per_share))
        holdings.append(
            Holding(
                ticker=ticker,
                name=str(row["name"]).strip(),
                asset_class=asset_class,
                shares=shares,
                cost_per_share=cost_per_share,
                price=price,
                sector=_clean_str(row.get("sector"), "その他"),
                market=_resolve_market(row.get("market"), ticker),
            )
        )
    return holdings


def _clean_str(value, default: str) -> str:
    """CSV由来の値を文字列化。空・NaN・None はデフォルトへ。"""
    if value is None:
        return default
    s = str(value).strip()
    if s == "" or s.lower() == "nan":
        return default
    return s


def _resolve_market(value, ticker: str) -> str:
    """market を解決。欠損時は ticker が4桁数字なら jp、他は us と推定。"""
    s = _clean_str(value, "")
    if s in ("jp", "us"):
        return s
    return "jp" if ticker.strip().isdigit() and len(ticker.strip()) == 4 else "us"


def total_cost(holdings: list[Holding]) -> float:
    """総取得額。"""
    return sum(h.cost_value for h in holdings)


def total_market(holdings: list[Holding]) -> float:
    """総評価額。"""
    return sum(h.market_value for h in holdings)


def total_gain(holdings: list[Holding]) -> float:
    """総含み損益。"""
    return total_market(holdings) - total_cost(holdings)


def total_gain_rate(holdings: list[Holding]) -> float:
    """総含み損益率（%）。取得額0なら0。"""
    cost = total_cost(holdings)
    if cost == 0:
        return 0.0
    return total_gain(holdings) / cost * 100.0


def allocation_by_class(holdings: list[Holding]) -> dict[str, float]:
    """資産クラス別の評価額構成比（%）。全クラスをキーに持つ（保有0なら0%）。"""
    market = total_market(holdings)
    sums = {ac: 0.0 for ac in ASSET_CLASSES}
    for h in holdings:
        sums[h.asset_class] += h.market_value
    if market == 0:
        return {ac: 0.0 for ac in ASSET_CLASSES}
    return {ac: sums[ac] / market * 100.0 for ac in ASSET_CLASSES}


def allocation_drift(holdings: list[Holding]) -> dict[str, float]:
    """目標AAとのズレ（現在% − 目標%）。正=オーバーウェイト、負=アンダーウェイト。"""
    current = allocation_by_class(holdings)
    return {ac: current[ac] - TARGET_ALLOCATION[ac] for ac in ASSET_CLASSES}
