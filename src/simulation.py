"""資産形成シミュレーション（純関数群）。

portfolio / dividend と同方針で外部依存を持たない。将来値・価格履歴は
すべて引数で注入し、認証情報・通信なしでテストできるようにする。

重要：本モジュールは「入力した前提でいくらになるか」を計算するだけであり、
将来を予測するものでも、特定の運用を推奨するものでもない。既定値は前提値であって
推奨値ではない（UI側でその旨を明示し、すべて変更可能にする）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from dividend import after_tax

# --- 入力欄の初期値（推奨値ではない。すべてUIで変更できる） ---
DEFAULT_ANNUAL_RETURN = 5.0  # 年率リターン想定（%）
DEFAULT_DIVIDEND_GROWTH = 3.0  # 増配率想定（%/年）
DEFAULT_MONTHLY_CONTRIBUTION = 130_000.0  # 毎月の入金額
DEFAULT_TARGET_AGE = 55  # 配当CF目標の到達年齢

# 配当CF目標帯（月額・円）の初期値。グラフに目標帯として重ねる。
TARGET_CF_MONTHLY_MIN = 60_000.0
TARGET_CF_MONTHLY_MAX = 100_000.0

MONTHS_PER_YEAR = 12


def age_at(birth: date, today: date) -> int:
    """today 時点の満年齢。誕生日前なら1つ下の年齢を返す。

    2/29 生まれは平年では 3/1 に歳を取る扱い（(month, day) の比較で自然にそうなる）。
    """
    years = today.year - birth.year
    if (today.month, today.day) < (birth.month, birth.day):
        years -= 1
    return years


def years_until_age(birth: date, today: date, target_age: int) -> int:
    """target_age に到達するまでの残り年数。既に超えていれば0。"""
    return max(0, target_age - age_at(birth, today))


@dataclass(frozen=True)
class YearPoint:
    """シミュレーション1年分の断面。"""

    year: int  # 経過年（0=現在）
    principal: float  # 投下元本累計（初期資産＋積立累計）
    value: float  # 評価額


def project_accumulation(
    initial: float, monthly: float, years: int, annual_return: float
) -> list[YearPoint]:
    """積立の将来推移を月次複利で計算し、年次の断面を返す。

    initial: 現在の評価額 / monthly: 毎月の積立額 / annual_return: 年率リターン（%）
    返り値は経過年0（現在）から years までの各年。
    """
    monthly_rate = annual_return / 100.0 / MONTHS_PER_YEAR
    points = [YearPoint(year=0, principal=initial, value=initial)]

    value = initial
    principal = initial
    for y in range(1, years + 1):
        for _ in range(MONTHS_PER_YEAR):
            # 月初に積み立て、その月の運用リターンが乗る想定
            value = (value + monthly) * (1.0 + monthly_rate)
            principal += monthly
        points.append(YearPoint(year=y, principal=principal, value=value))
    return points


@dataclass(frozen=True)
class DividendPoint:
    """配当CFシミュレーション1年分の断面。"""

    year: int  # 経過年（0=現在）
    annual_pre_tax: float  # 年間配当（税込）
    annual_after_tax: float  # 年間配当（税抜）

    @property
    def monthly_pre_tax(self) -> float:
        return self.annual_pre_tax / MONTHS_PER_YEAR

    @property
    def monthly_after_tax(self) -> float:
        return self.annual_after_tax / MONTHS_PER_YEAR


def project_dividend_cf(
    current_annual_dividend: float,
    monthly: float,
    years: int,
    dividend_yield: float,
    dividend_growth: float,
    income_ratio: float,
    tax_market: str = "jp",
) -> list[DividendPoint]:
    """配当キャッシュフローの将来推移を年次で返す。

    モデル（単純化した前提）：
    - 既存の配当は毎年 dividend_growth%（増配率）で成長する
    - 毎月の積立のうち income_ratio（0〜1）の割合が配当を生む資産クラスへ向かい、
      買い付けた分がその年から dividend_yield% の配当を生む
    - 買い付け済みの分も翌年以降は増配率で成長する

    税抜は tax_market の税率で一律換算する（日米混在の厳密配分はしない保守表示）。
    """
    growth = dividend_growth / 100.0
    yield_rate = dividend_yield / 100.0
    annual_income_contribution = monthly * MONTHS_PER_YEAR * income_ratio

    points: list[DividendPoint] = [
        DividendPoint(
            year=0,
            annual_pre_tax=current_annual_dividend,
            annual_after_tax=after_tax(current_annual_dividend, tax_market),
        )
    ]

    annual = current_annual_dividend
    for y in range(1, years + 1):
        # 既存分は増配、その年の買い付け分が新たな配当を上乗せする
        annual = annual * (1.0 + growth) + annual_income_contribution * yield_rate
        points.append(
            DividendPoint(
                year=y,
                annual_pre_tax=annual,
                annual_after_tax=after_tax(annual, tax_market),
            )
        )
    return points


def first_year_reaching(
    points: list[DividendPoint], monthly_target: float, pre_tax: bool = False
) -> int | None:
    """月額 monthly_target に到達する最初の経過年。届かなければ None。

    既定では税抜（手取り）で判定する＝生活費に充てられる額で見るため。
    """
    for p in points:
        value = p.monthly_pre_tax if pre_tax else p.monthly_after_tax
        if value >= monthly_target:
            return p.year
    return None


@dataclass(frozen=True)
class BacktestResult:
    """積立バックテストの結果。"""

    series: list[tuple[date, float, float]]  # (日付, 投下元本累計, 評価額)
    invested: float  # 投下元本合計
    final_value: float  # 最終評価額
    return_rate: float  # トータルリターン（%）
    worst_unrealized_rate: float  # 最大含み損率（%・0以下。含み損にならなければ0）
    # 注：評価額ピークからの下落（一般的な最大ドローダウン）は積立では歪む。
    # 毎月の買い増しでピークが更新され続けるため下落を表さない。積立の実感に
    # 沿う「投下元本に対して最悪どれだけマイナスだったか」を採る。

    @property
    def months(self) -> int:
        return len(self.series)


# 月次リターンがこの範囲を外れたら価格が不連続＝株式分割等が未調整と判断する。
# 実際の相場では月次 -50% / +100% はまず起きない（暴落局面でも月次 -25% 程度）。
DISCONTINUITY_DROP = -0.5
DISCONTINUITY_RISE = 1.0


def find_discontinuous(history: dict[str, list[tuple[date, float]]]) -> list[str]:
    """価格が不連続な銘柄（株式分割が未調整の疑い）を返す。

    yfinance は日本のETFについて分割情報を返さないことがあり、その場合
    過去価格が分割前の実額のまま残って見かけ上の暴落・暴騰になる（実例：
    2559 が5年で -78%、1489 が +300%）。この状態で積立を再現すると結果が
    無意味になるため、呼び出し側が除外できるよう検出だけを行う。
    """
    bad: list[str] = []
    for ticker, series in history.items():
        for (_, prev), (_, cur) in zip(series, series[1:]):
            if prev <= 0:
                continue
            change = cur / prev - 1.0
            if change <= DISCONTINUITY_DROP or change >= DISCONTINUITY_RISE:
                bad.append(ticker)
                break
    return bad


def backtest_dca(
    history: dict[str, list[tuple[date, float]]],
    weights: dict[str, float],
    monthly: float,
) -> BacktestResult:
    """月次の定額積立（ドルコスト平均法）を価格履歴で再現する。

    history: {ticker: [(日付, 価格)]}（月次・昇順）。呼び出し側が注入する（通信はしない）
    weights: {ticker: 配分比率}。合計が1でなくても比率として正規化する
    monthly: 毎月の積立額

    全ticker に価格がある月だけを対象にする（履歴の短い銘柄に期間を合わせる）。
    価格が1つも揃わない場合は空の結果を返す。
    """
    usable = {t: dict(h) for t, h in history.items() if t in weights and h}
    if not usable:
        return BacktestResult(series=[], invested=0.0, final_value=0.0,
                              return_rate=0.0, worst_unrealized_rate=0.0)

    # 全銘柄に価格が存在する月だけを使う（共通期間）
    common_dates = set.intersection(*(set(prices) for prices in usable.values()))
    dates = sorted(common_dates)
    if not dates:
        return BacktestResult(series=[], invested=0.0, final_value=0.0,
                              return_rate=0.0, worst_unrealized_rate=0.0)

    total_weight = sum(weights[t] for t in usable)
    if total_weight <= 0:
        return BacktestResult(series=[], invested=0.0, final_value=0.0,
                              return_rate=0.0, worst_unrealized_rate=0.0)

    shares = {t: 0.0 for t in usable}
    invested = 0.0
    series: list[tuple[date, float, float]] = []
    worst_unrealized = 0.0

    for d in dates:
        # その月の配分比で買い付ける
        for ticker in usable:
            price = usable[ticker][d]
            if price <= 0:
                continue
            amount = monthly * (weights[ticker] / total_weight)
            shares[ticker] += amount / price
        invested += monthly

        value = sum(shares[t] * usable[t][d] for t in usable)
        series.append((d, invested, value))

        if invested > 0:
            unrealized = (value - invested) / invested * 100.0
            worst_unrealized = min(worst_unrealized, unrealized)

    final_value = series[-1][2]
    return_rate = (final_value - invested) / invested * 100.0 if invested else 0.0
    return BacktestResult(
        series=series,
        invested=invested,
        final_value=final_value,
        return_rate=return_rate,
        worst_unrealized_rate=worst_unrealized,
    )
