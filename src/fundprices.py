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
from datetime import date

CSV_URL = "https://toushin-lib.fwg.ne.jp/FdsWeb/FDST030000/csv-file-download"
TIMEOUT_SECONDS = 20
ENCODING = "cp932"  # 協会CSVは Shift-JIS

# 基準価額は1万口あたりの金額。1口あたりへ換算する係数（rakuten_all.py と同じ意味）
FUND_PRICE_UNIT = 10000.0

_DATE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


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


def fetch_nav(isin: str, assoc_fund_cd: str) -> tuple[date, float] | None:
    """1ファンドの最新 (日付, 1口あたり基準価額) を返す。取得不可なら None。"""
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
        return parse_nav_csv(res.content.decode(ENCODING, errors="replace"))
    except Exception:
        # 通信不可・想定外のレスポンス。呼び出し側は既存の price を残す
        return None


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
