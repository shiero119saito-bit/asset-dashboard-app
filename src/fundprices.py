"""投資信託の基準価額取得（外部依存＝投資信託協会の公開CSVを隔離）。

日本の投資信託は上場していないため yfinance には存在しない（`prices.is_fetchable` が
弾く）。そのため取込時の取得単価のまま評価され、含み益が丸ごと欠落していた
（2026-09-05 に実測：9本・取得額376万に対し評価額が同額のまま＝約294万の過少計上）。

投資信託協会が日次の基準価額CSVを公開しているのでそれを使う。スクレイピングではなく
公式のダウンロード機能で、ISINコードと協会ファンドコードの2つで1ファンドが特定される。

    年月日,基準価額(円),純資産総額（百万円）,分配金,決算期
    2026年09月04日,37945,13642433,,

**基準価額は1万口あたり**で記載される。一方 holdings.csv の cost_per_share は1口あたり
（`importers/rakuten_all.py` が FUND_PRICE_UNIT で換算済み）なので、ここでも1口あたりに
揃えて返す。単位を混ぜると評価額が1万倍ずれる。

prices.py と同じ方針で、失敗時は例外を投げずキーを省略する（呼び出し側は既存の値を残す）。
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta

CSV_URL = "https://toushin-lib.fwg.ne.jp/FdsWeb/FDST030000/csv-file-download"
TIMEOUT_SECONDS = 20
ENCODING = "cp932"  # 協会CSVは Shift-JIS

# 基準価額は1万口あたりの金額。1口あたりへ換算する係数（rakuten_all.py と同じ意味）
FUND_PRICE_UNIT = 10000.0

_DATE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")

# 協会CSVの列。分配金は決算日の行にだけ入り、他の日は空欄
_NAV_COLUMN = 1
_DIVIDEND_COLUMN = 3

# 年間分配金とみなす遡り期間（決算が四半期なら4回分が入る）
_TRAILING_DAYS = 365


def parse_nav_csv(text: str) -> tuple[date, float] | None:
    """協会CSVの本文から最新の (日付, 1口あたり基準価額) を返す。取れなければ None。

    行は日付の昇順で並ぶため末尾が最新。ただし末尾に空行や壊れた行が混じりうるので、
    後ろから順に「日付と数値が両方読める行」を探す（1行の欠損で全体を捨てない）。
    """
    rows = list(csv.reader(io.StringIO(text)))
    for row in reversed(rows):
        if len(row) < 2:
            continue
        m = _DATE.search(row[0].strip())
        if not m:
            continue  # ヘッダ行や注記行
        try:
            nav = float(row[1].strip().replace(",", ""))
        except ValueError:
            continue
        if nav <= 0:
            continue
        y, mo, d = (int(g) for g in m.groups())
        return (date(y, mo, d), nav / FUND_PRICE_UNIT)
    return None


def parse_annual_dividend_csv(text: str) -> float | None:
    """協会CSV本文から**直近1年の分配金合計（1口あたり）**を返す。行が無ければ None。

    投資信託は yfinance に存在しないため、分配金も同じCSVから取るしかない
    （楽天・SCHD の分配金が丸ごと配当に計上されていなかった）。
    分配金も基準価額と同じく**1万口あたり**で記載されるので1口あたりへ換算する。
    """
    entries: list[tuple[date, float]] = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) <= _DIVIDEND_COLUMN:
            continue
        matched = _DATE.search(row[0].strip())
        if not matched:
            continue  # ヘッダ行・注記行
        raw = row[_DIVIDEND_COLUMN].strip().replace(",", "")
        try:
            amount = float(raw) if raw else 0.0
        except ValueError:
            continue
        year, month, day = (int(g) for g in matched.groups())
        entries.append((date(year, month, day), amount))

    if not entries:
        return None
    latest = max(day for day, _ in entries)
    cutoff = latest - timedelta(days=_TRAILING_DAYS)
    total = sum(amount for day, amount in entries if day > cutoff)
    return total / FUND_PRICE_UNIT


def fetch_fund_csv(isin: str, assoc_fund_cd: str) -> str | None:
    """1ファンドの協会CSV本文を取得する。取得不可なら None。"""
    if not isin or not assoc_fund_cd:
        return None
    try:
        import requests
    except ImportError:
        return None
    try:
        res = requests.get(
            CSV_URL,
            params={"isinCd": isin.strip(), "associFundCd": assoc_fund_cd.strip()},
            timeout=TIMEOUT_SECONDS,
        )
        if res.status_code != 200 or not res.content:
            return None
        return res.content.decode(ENCODING, errors="replace")
    except Exception:
        # 通信不可・想定外のレスポンス。呼び出し側は既存の値を残す
        return None


def fetch_annual_dividends(funds: dict[str, tuple[str, str]]) -> dict[str, float]:
    """{ticker: (isin, assoc_fund_cd)} から {ticker: 1口あたり年間分配金} を返す。

    分配金が0のファンド（無分配型）はキーを省略しない＝0として返す。
    取得自体に失敗したファンドはキーを省略する（`fetch_navs` と同じ約束）。
    """
    result: dict[str, float] = {}
    for ticker, (isin, assoc) in funds.items():
        text = fetch_fund_csv(isin, assoc)
        if text is None:
            continue
        amount = parse_annual_dividend_csv(text)
        if amount is not None:
            result[ticker] = amount
    return result


def fetch_nav(isin: str, assoc_fund_cd: str) -> tuple[date, float] | None:
    """1ファンドの最新 (日付, 1口あたり基準価額) を返す。取得不可なら None。"""
    text = fetch_fund_csv(isin, assoc_fund_cd)
    return parse_nav_csv(text) if text else None


def fetch_navs(funds: dict[str, tuple[str, str]]) -> dict[str, float]:
    """{ticker: (isin, assoc_fund_cd)} から {ticker: 1口あたり基準価額} を返す。

    取得できなかったファンドはキーを省略する（`prices.fetch_prices` と同じ約束）。
    """
    result: dict[str, float] = {}
    for ticker, (isin, assoc) in funds.items():
        nav = fetch_nav(isin, assoc)
        if nav:
            result[ticker] = nav[1]
    return result
