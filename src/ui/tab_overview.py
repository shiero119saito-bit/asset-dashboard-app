"""概要タブ：全体像だけを見る場所。深掘りは各タブへ送る。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import cash as ca
import dataio
import dividend as dv
import portfolio as pf

from ui.format import yen
from ui.widgets import line_chart, show_table, snapshot_notice


def render(holdings, div_map, cash_rows, snapshot_rows, goals, cfg) -> None:
    """全体像だけを見る場所。深掘りは各タブへ送る。"""
    market = pf.total_market(holdings)

    st.subheader("総資産の推移")
    if snapshot_notice(snapshot_rows):
        line_chart(snapshot_rows, {"net_worth": "総資産", "total_cost": "投下元本"},
                   "総資産と投下元本")

    left, right = st.columns([1, 1])

    with left:
        st.subheader("資産構成")
        alloc = pf.allocation_by_class(holdings)
        total_assets = ca.net_worth(market, cash_rows)
        composition = {"現金・預金": ca.total(cash_rows)}
        for asset_class in pf.ASSET_CLASSES:
            composition[pf.ASSET_CLASS_LABELS[asset_class]] = market * alloc[asset_class] / 100.0
        comp_df = pd.DataFrame({
            "区分": list(composition),
            "金額": [round(v) for v in composition.values()],
            "構成比%": [round(v / total_assets * 100, 1) if total_assets else 0.0
                        for v in composition.values()],
        })
        show_table(comp_df, order_key="cols_composition", cfg=cfg)
        if not cash_rows:
            st.caption("現金が未入力のため、総資産＝運用資産で表示している（データタブで入力できる）。")

    with right:
        st.subheader("配当サマリー")
        annual_pre = dv.total_annual_dividend(holdings, div_map, pre_tax=True)
        annual_after = dv.total_annual_dividend(holdings, div_map, pre_tax=False)
        summary = pd.DataFrame({
            "項目": ["年間予想（税込）", "年間予想（税抜）", "月平均（税抜）",
                     "取得額利回り", "評価額利回り"],
            "値": [yen(annual_pre), yen(annual_after), yen(annual_after / 12),
                   f"{dv.yield_on_cost(holdings, div_map):.2f}%",
                   f"{dv.yield_on_market(holdings, div_map):.2f}%"],
        })
        show_table(summary, order_key="cols_div_summary", cfg=cfg)

        st.subheader("目標サマリー")
        _render_goal_bars(holdings, div_map, cash_rows, goals, compact=True)

    st.subheader("次に買うなら")
    st.caption("目標AAとの差を金額で出す。売却は前提にせず、買い増しだけで寄せる。")
    _render_rebalance(holdings, cfg)


@st.fragment
def _render_rebalance(holdings, cfg) -> None:
    """目標AAとの差額と、追加投資額の配分案。

    fragment＝追加投資額を入れ直しても再計算はこの表だけで済む。
    """
    gaps = pf.rebalance_amounts(holdings)
    extra = st.number_input(
        "追加投資額（円）", value=0, min_value=0, step=10000, key="rebalance_extra",
        help="入れると不足の大きい順に配分する。0のままなら差額だけを表示する",
    )
    plan = pf.allocate_new_money(holdings, float(extra)) if extra else {}
    table = pd.DataFrame([
        {
            "資産クラス": pf.ASSET_CLASS_LABELS[ac],
            "現在": round(gaps[ac]["current"]),
            "目標": round(gaps[ac]["target"]),
            "差額": round(gaps[ac]["diff"]),
            "配分案": round(plan.get(ac, 0.0)),
        }
        for ac in pf.ASSET_CLASSES
    ]).sort_values("差額", ascending=False)
    show_table(table, order_key="cols_rebalance", cfg=cfg)
    st.caption("差額がプラス＝不足（買い増し候補）、マイナス＝目標より多い。")


def _render_goal_bars(holdings, div_map, cash_rows, goals, compact: bool = False) -> None:
    """目標ごとの達成率。配当は税抜（手取り）で見る＝受け取れる額が目標だから。"""
    annual_after = dv.total_annual_dividend(holdings, div_map, pre_tax=False)
    total_assets = ca.net_worth(pf.total_market(holdings), cash_rows)
    items = [
        ("年間配当（税抜）", annual_after, goals["goal_dividend_annual"]),
        ("月間配当（税抜）", annual_after / 12, goals["goal_dividend_annual"] / 12),
        ("総資産", total_assets, goals["goal_net_worth"]),
    ]
    for label, current, goal in items:
        progress = dataio.goal_progress(current, goal)
        st.write(f"**{label}**　{yen(current)} / {yen(goal)}　（{progress:.1f}%）")
        st.progress(min(progress / 100.0, 1.0))
        if not compact:
            remaining = max(0.0, goal - current)
            st.caption(f"残り {yen(remaining)}")
