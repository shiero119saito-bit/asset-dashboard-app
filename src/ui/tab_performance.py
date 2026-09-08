"""資産・成績タブ：増えた理由を分解する場所。入金・値動き・配当を分けて見る。"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

import dividend_history as dh
import portfolio as pf
import snapshots as sn

from ui.format import yen
from ui.widgets import line_chart, show_table, snapshot_notice


def render(holdings, snapshot_rows, history_rows, cfg) -> None:
    """増えた理由を分解する場所。入金・値動き・配当を分けて見る。"""
    cost = pf.total_cost(holdings)
    gain = pf.total_gain(holdings)
    received = sum(dh.by_year(history_rows).values())

    st.subheader("トータルリターン")
    st.caption("株価が上がったから増えた、だけではないことを見る。")
    r1, r2, r3 = st.columns(3)
    r1.metric("評価損益", yen(gain))
    r2.metric("累計受取配当", yen(received) if history_rows else "未記録")
    r3.metric("総合収益", yen(gain + received),
              f"{(gain + received) / cost * 100:+.1f}%（元本比）" if cost else None)
    st.caption("年率リターン（XIRR）は取得日を記録していないため出していない。")

    st.subheader("直近の増減の内訳")
    contributions = sn.deltas(snapshot_rows, "total_cost")
    market_deltas = sn.deltas(snapshot_rows, "total_market")
    if not contributions:
        snapshot_notice(snapshot_rows)
    else:
        when, contributed = contributions[-1]
        _, market_delta = market_deltas[-1]
        breakdown = pd.DataFrame({
            "内訳": ["入金（投下元本の増加）", "値動き", "運用資産の増加"],
            "金額": [round(contributed), round(market_delta - contributed), round(market_delta)],
        })
        show_table(breakdown, order_key="cols_breakdown", cfg=cfg)
        st.caption(
            f"{when} の記録と1つ前の記録の差。**入金は投下元本の増加で近似**している"
            "（入出金の台帳を持っていないため）。売却した月は元本が減るのでマイナスになる。"
            "受取配当は運用資産の外に入るため、上の「累計受取配当」で別に見る。"
        )

        st.subheader("入金力")
        amounts = [amount for _, amount in contributions]
        this_year = str(date.today().year)
        year_total = sum(a for d, a in contributions if d.startswith(this_year))
        c1, c2, c3 = st.columns(3)
        c1.metric("直近の入金", yen(amounts[-1]))
        c2.metric(f"{this_year}年の入金", yen(year_total))
        c3.metric("平均入金（記録期間）", yen(sum(amounts) / len(amounts)))

    st.subheader("運用資産・元本・現金の推移")
    if snapshot_notice(snapshot_rows):
        line_chart(
            snapshot_rows,
            {"total_market": "運用資産", "total_cost": "投下元本", "cash": "現金"},
            "評価額と元本の開きが含み益、現金の増減が待機資金の動き",
        )
