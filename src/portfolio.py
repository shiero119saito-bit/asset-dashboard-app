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

# 業種（東証33業種）。個別株の業種分散を見るための区分で、asset_class（戦略上の資産クラス）や
# sector（商品種別）とは別軸。末尾2つは個別株でない保有の受け皿＝これにより業種軸でも
# 全資産の構成比が100%になる。表示順もこの並びに従う。
INDUSTRIES = (
    "水産・農林業", "鉱業", "建設業", "食料品", "繊維製品", "パルプ・紙", "化学", "医薬品",
    "石油・石炭製品", "ゴム製品", "ガラス・土石製品", "鉄鋼", "非鉄金属", "金属製品", "機械",
    "電気機器", "輸送用機器", "精密機器", "その他製品", "電気・ガス業", "陸運業", "海運業",
    "空運業", "倉庫・運輸関連業", "情報・通信業", "卸売業", "小売業", "銀行業",
    "証券、商品先物取引業", "保険業", "その他金融業", "不動産業", "サービス業",
    "ETF・投信", "REIT",
)

# 業種が空欄の保有をまとめる表示名（集計キー）
INDUSTRY_UNCLASSIFIED = "未分類"

# 目標アセットアロケーション（%）。運用方針に合わせて変更する。
TARGET_ALLOCATION = {
    "index": 60.0,
    "us_dividend": 20.0,
    "jp_dividend": 15.0,
    "reit": 5.0,
}


@dataclass(frozen=True)
class Holding:
    """保有1銘柄。cost_per_share・shares は取得時情報、price は現在値。

    sector・industry・market は配当/集計の切り口に使う静的メタ（CSV由来）。
    変動する配当額は Holding に持たせず、dividend モジュールで div_map を注入する。
    """

    ticker: str
    name: str
    asset_class: str
    shares: float
    cost_per_share: float
    price: float
    sector: str = "その他"
    # 業種（東証33業種）。sector＝商品種別とは別軸。空欄は未分類として集計する
    industry: str = ""
    market: str = "us"
    purpose: str = ""
    source: str = ""
    # 口座区分（specific/nisa_old/nisa_tsumitate/nisa_growth）。配当の税率が変わるため、
    # 同一銘柄でも口座別に別の Holding として持つ。表示は group_by_ticker でまとめる
    account: str = ""
    price_asof: str = ""  # CSV保存の時価がいつ時点か（ライブ取得時は空）

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
        price = _resolve_price(ticker, row, price_map, cost_per_share)
        holdings.append(
            Holding(
                ticker=ticker,
                name=str(row["name"]).strip(),
                asset_class=asset_class,
                shares=shares,
                cost_per_share=cost_per_share,
                price=price,
                sector=_clean_str(row.get("sector"), "その他"),
                industry=_clean_str(row.get("industry"), ""),
                market=_resolve_market(row.get("market"), ticker),
                purpose=_clean_str(row.get("purpose"), ""),
                source=_clean_str(row.get("source"), ""),
                account=_clean_str(row.get("account"), ""),
                # ライブ取得できた銘柄は「現在値」なので asof は付けない
                price_asof="" if price_map.get(ticker) else _clean_str(row.get("price_asof"), ""),
            )
        )
    return holdings


def _resolve_price(
    ticker: str, row: dict, price_map: dict[str, float], cost_per_share: float
) -> float:
    """現在値を3段で解決する：ライブ時価 → CSVに保存された時価 → 取得単価。

    CSVの `price` 列は取込時（ローカル）に保存した時価。Streamlit Cloud からは
    Yahoo Finance が HTTP 401 を返しライブ取得ができないため、そこでの表示は
    この保存値に頼る。いつ時点かは `price_asof` 列に持ち、UI が明示する。
    """
    live = price_map.get(ticker)
    if live:
        return float(live)
    saved = _clean_str(row.get("price"), "")
    if saved:
        try:
            value = float(saved)
            if value > 0:
                return value
        except ValueError:
            pass
    return cost_per_share


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


def _allocation_by_key(holdings: list[Holding], key_fn) -> dict[str, float]:
    """key_fn の値別に評価額構成比（%）を集計する。保有0なら空dict。

    allocation_by_class と違いキーが動的（CSV由来の値次第）なため、
    全キーを0で埋めることはしない。
    """
    market = total_market(holdings)
    if market == 0:
        return {}
    sums: dict[str, float] = {}
    for h in holdings:
        key = key_fn(h)
        sums[key] = sums.get(key, 0.0) + h.market_value
    return {k: v / market * 100.0 for k, v in sums.items()}


def allocation_by_sector(holdings: list[Holding]) -> dict[str, float]:
    """sector 列別の評価額構成比（%）。

    注意：実データの sector は業種（電気機器・銀行 等）ではなく
    商品種別（個別株/投資信託/ETF/REIT）。業種分散の可視化には別途データ取得が要る。
    """
    return _allocation_by_key(holdings, lambda h: h.sector)


def allocation_by_market_region(holdings: list[Holding]) -> dict[str, float]:
    """market 列別の評価額構成比（%）。

    注意：market は上場市場（jp/us）であり投資対象地域ではない。
    東証上場のオルカン・S&P500 ETF/投信は jp に計上される。
    """
    return _allocation_by_key(holdings, lambda h: h.market)


def allocation_by_account(holdings: list[Holding]) -> dict[str, float]:
    """口座区分別の評価額構成比（%）。NISA にどれだけ入っているかを見る。

    空欄は特定口座として集計する（税計算と同じ扱いに揃える）。
    """
    return _allocation_by_key(holdings, lambda h: h.account or "specific")


# 円グラフで小さすぎるスライスをまとめる既定しきい値（%）。これ未満はラベルが重なって
# 読めなくなるため1つに集約する。集約後の表示名は OTHER_SLICE_LABEL
SMALL_SLICE_THRESHOLD = 2.5
OTHER_SLICE_LABEL = "その他"


def group_small_slices(
    values: dict[str, float],
    threshold_pct: float = SMALL_SLICE_THRESHOLD,
    other_label: str = OTHER_SLICE_LABEL,
) -> dict[str, float]:
    """円グラフ用に、全体比が threshold 未満のキーを1つへまとめる。

    業種のようにキーが20を超える軸では、小さいスライスのラベルが重なって
    「どれがどれか分からない」状態になる。明細は表側で見られるため、図は上位だけ残す。

    まとめる対象が1件しかないときは、その名前のまま残す（「その他」に置き換えても
    情報が減るだけで読みやすくならないため）。合計が0以下なら何もしない。
    """
    total = sum(v for v in values.values() if v > 0)
    if total <= 0:
        return dict(values)

    kept: dict[str, float] = {}
    small: dict[str, float] = {}
    for key, value in values.items():
        if 0 < value and value / total * 100.0 < threshold_pct:
            small[key] = value
        else:
            kept[key] = value

    if len(small) == 1:
        kept.update(small)
    elif small:
        kept[other_label] = sum(small.values())
    return kept


def allocation_by_industry(holdings: list[Holding]) -> dict[str, float]:
    """業種別の評価額構成比（%）。空欄は「未分類」に寄せる。

    ETF・投信/REIT も INDUSTRIES の受け皿カテゴリに入るため、全保有を渡せば合計100%になる。
    個別株だけの業種分散を見たいときは jp_stocks_only で絞ってから渡す。
    """
    return _allocation_by_key(holdings, lambda h: h.industry or INDUSTRY_UNCLASSIFIED)


def jp_stocks_only(holdings: list[Holding]) -> list[Holding]:
    """日本個別株（asset_class == "jp_dividend"）だけに絞る。

    業種分散はETF・投信を混ぜると「ETF・投信が大半」で終わってしまうため、
    個別株だけの内訳を見るための絞り込み。元リストは変更しない。
    """
    return [h for h in holdings if h.asset_class == "jp_dividend"]


def group_by_ticker(holdings: list[Holding]) -> dict[str, list[Holding]]:
    """同一銘柄の保有を口座をまたいでまとめる（表示用）。

    税率が口座で変わるため、計算は口座別の Holding 単位で行う必要がある。
    一方、画面で同じ銘柄が何行にも分かれて出ると読みにくいので、表示のときだけまとめる。
    出現順を保つ（並べ替えは呼び出し側の責務）。
    """
    groups: dict[str, list[Holding]] = {}
    for h in holdings:
        groups.setdefault(h.ticker, []).append(h)
    return groups


def merged_cost_per_share(group: list[Holding]) -> float:
    """口座をまたいだ取得単価（加重平均）。株数0なら0。

    単価の単純平均ではなく取得額の合計を株数の合計で割る。口座ごとに取得時期が違えば
    単価も違うため、単純平均だと少数株の口座が過大に効いてしまう。
    """
    shares = sum(h.shares for h in group)
    if shares == 0:
        return 0.0
    return sum(h.cost_value for h in group) / shares


def filter_by_purpose(holdings: list[Holding], purposes) -> list[Holding]:
    """保有目的（purpose）で保有を絞り込む。purposes が空なら全件返す。

    配当画面で「配当目的の資産だけ」を見るための表示フィルタ。AA計算や税率には
    影響しない＝純粋に対象集合を絞るだけ。値は大小文字・前後空白を無視して比較する。
    """
    if not purposes:
        return list(holdings)
    wanted = {str(p or "").strip().lower() for p in purposes}
    return [h for h in holdings if str(h.purpose or "").strip().lower() in wanted]


def jp_dividend_by_purpose(holdings: list[Holding]) -> dict[str, list[Holding]]:
    """日本高配当(jp_dividend)保有を purpose（dividend/yutai/未分類）で分類する。

    AA計算（allocation_by_class 等）には影響しない＝表示上のグルーピングのみ。
    """
    groups: dict[str, list[Holding]] = {"dividend": [], "yutai": [], "": []}
    for h in holdings:
        if h.asset_class != "jp_dividend":
            continue
        groups.setdefault(h.purpose, []).append(h)
    return groups
