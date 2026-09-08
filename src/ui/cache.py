"""外部取得のキャッシュ層。

**画面は原則として外部から取得しない**。時価・配当・権利確定月・投信の分配金は
`scripts/refresh_prices.py`（PC・GitHub Actions）が holdings.csv に書き、画面は
その保存値を読むだけにしている。Streamlit Cloud からは Yahoo が 401 を返すため、
画面から取りに行くと「待たされた末に取れない」だけになるため（2026-09-08）。

ここに残っているのはバックテストの価格履歴だけ。**ボタンで明示実行する**ので
初回表示の待ち時間には乗らない。prices.py は純粋なまま保つ（設計方針）ため、
Streamlit 依存の @st.cache_data はこのモジュールに閉じ込める。
"""
from __future__ import annotations

import streamlit as st

import prices as pr


@st.cache_data(persist="disk", show_spinner="価格履歴を取得中…")
def cached_price_history(tickers: tuple[str, ...], years: int):
    """バックテスト用の価格履歴。取得が重いためディスクにも残す（再起動後も再利用）。"""
    return pr.fetch_price_history(list(tickers), years)
