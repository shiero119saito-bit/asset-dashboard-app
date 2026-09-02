"""時価取得（外部依存=yfinance を隔離）。

ticker の解決ルール：
- 日本株/ETF（数字4桁）は yfinance では `.T` サフィックスが必要（例 1343 → 1343.T）
- 米国ティッカー（SCHD/VYM 等）は素のまま

オフライン時・取得失敗時は空 dict / 欠損を返し、呼び出し側（portfolio.build_holdings）が
取得単価でフォールバックする。テスト時はこのモジュールを呼ばず price_map をスタブ注入する。
"""
from __future__ import annotations

import re
from datetime import date

_JP_CODE = re.compile(r"^\d{4}$")


def to_yf_symbol(ticker: str) -> str:
    """ローカル ticker を yfinance シンボルへ変換する。"""
    t = ticker.strip()
    if _JP_CODE.match(t):
        return f"{t}.T"
    return t


# yfinance に問い合わせられる ticker の形（英数字・ドット・ハイフンのみ）。
# 日本の投資信託は証券コードを持たず銘柄名がそのまま ticker になるため該当しない。
_FETCHABLE_TICKER = re.compile(r"^[A-Za-z0-9.\-]+$")


def is_fetchable(ticker: str) -> bool:
    """yfinance に問い合わせる価値のある ticker か。

    投資信託（例「ｅＭＡＸＩＳ Ｓｌｉｍ 全世界株式」）は上場しておらず yfinance に
    存在しない。問い合わせても必ず "possibly delisted" で失敗するうえ、銘柄数分の
    無駄なリクエストがレート制限を悪化させるため、呼ぶ前に弾く。
    """
    return bool(_FETCHABLE_TICKER.match(ticker.strip()))


def _to_camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(w.capitalize() for w in rest)


def fast_info_value(info, name: str) -> float | None:
    """FastInfo から値を取り出す（snake_case 名で指定）。取れなければ None。

    yfinance 1.7.0 で FastInfo の**辞書キーが snake_case から camelCase へ変わった**
    （`lastPrice` / `yearHigh`）。一方で属性アクセスは snake_case のまま使える。
    このため `.get("last_price")` は例外も出さず None を返し、時価・為替の取得が
    全銘柄で静かに失敗していた（2026-09-02 に発覚）。

    どちらの表記でも動くよう、属性→camelキー→snakeキーの順に試す。
    """
    for get in (
        lambda: getattr(info, name),
        lambda: info.get(_to_camel(name)),
        lambda: info.get(name),
    ):
        try:
            value = get()
        except Exception:
            continue
        if value:
            return float(value)
    return None


def fetch_prices(tickers: list[str]) -> dict[str, float]:
    """ticker リストの現在値を {ticker: price} で返す。失敗銘柄はキー省略。

    yfinance 未導入・通信不可の場合は空 dict を返す（フォールバック前提）。
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}

    result: dict[str, float] = {}
    for ticker in tickers:
        if not is_fetchable(ticker):
            continue  # 投資信託等：yfinance に存在せず必ず失敗する
        symbol = to_yf_symbol(ticker)
        try:
            price = fast_info_value(yf.Ticker(symbol).fast_info, "last_price")
            if price:
                result[ticker] = price
        except Exception:
            # 個別銘柄の失敗は無視（呼び出し側が取得単価でフォールバック）
            continue
    return result


FX_SYMBOL = "JPY=X"  # yfinance の USD/JPY ティッカー


def fetch_fx_rate() -> float | None:
    """USD/JPY の現在レートを返す。取得不可・未導入時は None。

    呼び出し側（app.py）は None を「ライブ換算不可」の合図として扱い、
    convert_us_values_to_jpy で該当ticker値をvalue_mapから除外させる。
    """
    try:
        import yfinance as yf
    except ImportError:
        return None
    try:
        return fast_info_value(yf.Ticker(FX_SYMBOL).fast_info, "last_price")
    except Exception:
        return None


def convert_us_values_to_jpy(
    value_map: dict[str, float], us_tickers: set[str], fx_rate: float | None
) -> dict[str, float]:
    """value_map（yfinance生値）のうちus_tickersに含まれる分をJPYへ換算する（純関数）。

    fx_rate が None（ライブ為替取得不可）の場合は該当ticker値をvalue_mapから除外する。
    呼び出し側（portfolio.build_holdings の price フォールバック等）はticker欠損を
    「取得単価（既にJPY換算済み）を使う」既存の安全機構として扱うため、円とドルが
    混在した値を返すことは絶対にない。
    """
    if not value_map or not us_tickers:
        return value_map
    result: dict[str, float] = {}
    for ticker, value in value_map.items():
        if ticker not in us_tickers:
            result[ticker] = value
        elif fx_rate is not None:
            result[ticker] = value * fx_rate
        # fx_rate が None の場合はキーごと除外（=result に追加しない）
    return result


def fetch_price_and_high(ticker: str) -> tuple[float, float]:
    """1銘柄の (現在値, 52週高値) を返す。取得不可は (0.0, 0.0)。

    投資判定（jquants.build_stockdata への株価注入）用。yfinance fast_info の
    last_price / year_high を使う。未導入・失敗時は (0.0, 0.0)（判定側でフォールバック）。
    """
    try:
        import yfinance as yf
    except ImportError:
        return (0.0, 0.0)
    try:
        info = yf.Ticker(to_yf_symbol(ticker)).fast_info
        price = fast_info_value(info, "last_price") or 0.0
        high = fast_info_value(info, "year_high") or 0.0
        return (price, high)
    except Exception:
        return (0.0, 0.0)


def fetch_annual_dividends_and_prices(
    ticker: str, years: int = 8
) -> tuple[dict[int, float], dict[int, float]]:
    """yfinance から年別の (年間配当合計, 年平均終値) を返す。失敗時 ({}, {})。

    過去N年平均利回りの算出元。当年は配当が部分的なので years に余裕（+1）を持たせて取得し、
    平均計算側（jquants.average_dividend_yield）で当年除外・直近7年抽出を行う前提。
    auto_adjust=False で実額の Close と Dividends を年で集計する。
    """
    try:
        import yfinance as yf
    except ImportError:
        return ({}, {})
    try:
        hist = yf.Ticker(to_yf_symbol(ticker)).history(period=f"{years}y", auto_adjust=False)
        if hist is None or len(hist) == 0:
            return ({}, {})
        hist = hist.copy()
        hist["Y"] = hist.index.year
        div_by_year: dict[int, float] = {}
        price_by_year: dict[int, float] = {}
        for y, grp in hist.groupby("Y"):
            d = float(grp["Dividends"].sum()) if "Dividends" in grp else 0.0
            p = float(grp["Close"].mean()) if "Close" in grp else 0.0
            if d > 0:
                div_by_year[int(y)] = d
            if p > 0:
                price_by_year[int(y)] = p
        return (div_by_year, price_by_year)
    except Exception:
        return ({}, {})


def fetch_dividends(tickers: list[str]) -> dict[str, float]:
    """ticker リストの年間配当/株を {ticker: 年間配当} で返す。

    直近12ヶ月の配当実績合計を優先し、無ければ info['dividendRate']。
    取得できない銘柄はキー省略（呼び出し側が CSV div_per_share or 0 でフォールバック）。
    """
    try:
        import pandas as pd
        import yfinance as yf
    except ImportError:
        return {}

    result: dict[str, float] = {}
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=365)
    for ticker in tickers:
        if not is_fetchable(ticker):
            continue  # 投資信託等：yfinance に存在せず必ず失敗する
        symbol = to_yf_symbol(ticker)
        try:
            tk = yf.Ticker(symbol)
            divs = tk.dividends  # pandas Series（index=支払日・tz-aware）
            annual = 0.0
            if divs is not None and len(divs) > 0:
                idx = divs.index
                # tz 揃え（naive の場合は cutoff を naive 比較に）
                cut = cutoff if idx.tz is not None else cutoff.tz_localize(None)
                annual = float(divs[idx >= cut].sum())
            if annual <= 0:
                rate = tk.info.get("dividendRate") if hasattr(tk, "info") else None
                annual = float(rate) if rate else 0.0
            if annual > 0:
                result[ticker] = annual
        except Exception:
            continue
    return result


def fetch_dividend_months(tickers: list[str]) -> dict[str, list[int]]:
    """ticker リストの権利確定（配当支払）月を {ticker: [月]} で返す。

    直近2年の配当支払月のユニーク値を採用。取得不可はキー省略。
    """
    try:
        import pandas as pd
        import yfinance as yf
    except ImportError:
        return {}

    result: dict[str, list[int]] = {}
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=730)
    for ticker in tickers:
        if not is_fetchable(ticker):
            continue  # 投資信託等：yfinance に存在せず必ず失敗する
        symbol = to_yf_symbol(ticker)
        try:
            divs = yf.Ticker(symbol).dividends
            if divs is None or len(divs) == 0:
                continue
            idx = divs.index
            cut = cutoff if idx.tz is not None else cutoff.tz_localize(None)
            months = sorted({int(d.month) for d in idx[idx >= cut]})
            if months:
                result[ticker] = months
        except Exception:
            continue
    return result


def fetch_price_history(tickers: list[str], years: int) -> dict[str, list[tuple[date, float]]]:
    """月次終値の履歴を {ticker: [(date, price)]} で返す（日付昇順）。

    バックテスト用。取得できない銘柄はキー省略、未導入・通信不可なら空 dict
    （fetch_prices と同じフォールバック方針）。日米混在でも通貨換算はしない
    ＝呼び出し側が同一通貨の銘柄で比較する前提。
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}

    result: dict[str, list[tuple[date, float]]] = {}
    for ticker in tickers:
        if not is_fetchable(ticker):
            continue  # 投資信託等：yfinance に存在せず必ず失敗する
        symbol = to_yf_symbol(ticker)
        try:
            hist = yf.Ticker(symbol).history(period=f"{years}y", interval="1mo")
            if hist is None or len(hist) == 0:
                continue
            series = [
                (idx.date(), float(close))
                for idx, close in hist["Close"].items()
                if close == close and close > 0  # NaN と 0 を除外
            ]
            if series:
                result[ticker] = sorted(series)
        except Exception:
            continue
    return result
