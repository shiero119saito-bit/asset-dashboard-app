"""時価取得（外部依存=yfinance を隔離）。

ticker の解決ルール：
- 日本株/ETF（数字4桁）は yfinance では `.T` サフィックスが必要（例 1343 → 1343.T）
- 米国ティッカー（SCHD/VYM 等）は素のまま

オフライン時・取得失敗時は空 dict / 欠損を返し、呼び出し側（portfolio.build_holdings）が
取得単価でフォールバックする。テスト時はこのモジュールを呼ばず price_map をスタブ注入する。
"""
from __future__ import annotations

import re

_JP_CODE = re.compile(r"^\d{4}$")


def to_yf_symbol(ticker: str) -> str:
    """ローカル ticker を yfinance シンボルへ変換する。"""
    t = ticker.strip()
    if _JP_CODE.match(t):
        return f"{t}.T"
    return t


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
        symbol = to_yf_symbol(ticker)
        try:
            info = yf.Ticker(symbol).fast_info
            price = info.get("last_price") if hasattr(info, "get") else info["last_price"]
            if price:
                result[ticker] = float(price)
        except Exception:
            # 個別銘柄の失敗は無視（呼び出し側が取得単価でフォールバック）
            continue
    return result


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
