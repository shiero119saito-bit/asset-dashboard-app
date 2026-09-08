"""収入計画タブ：事業（副業）・労働収入（パート）の実績と55歳想定月収の内訳。"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

import income as inc

from ui.format import yen
from ui.widgets import pie, show_table


def render(income_rows, goals, plan, cfg) -> None:
    """事業（副業）・労働収入（パート）の実績と、55歳想定月収の内訳。"""
    target_age = plan["target_age"]

    st.subheader(f"{target_age}歳の想定月収")
    total = plan["monthly_income"]
    breakdown = pd.DataFrame([
        {"区分": "配当（税抜・増配0%）", "月額": round(plan["dividend_future"] / 12),
         "目標": round(goals["goal_dividend_monthly"])},
        {"区分": "インデックス取り崩し", "月額": round(goals["goal_withdrawal_monthly"]),
         "目標": round(goals["goal_withdrawal_monthly"])},
        {"区分": "事業", "月額": round(plan["business"]),
         "目標": round(goals["goal_business_monthly"])},
        {"区分": "労働収入", "月額": round(plan["labor"]),
         "目標": round(goals["goal_labor_monthly"])},
    ])
    left, right = st.columns([1, 1])
    left.plotly_chart(
        pie(dict(zip(breakdown["区分"], breakdown["月額"])), "区分",
            f"{target_age}歳の月収内訳", group_small=False),
        width="stretch",
    )
    show_table(breakdown, right, order_key="cols_income_breakdown", cfg=cfg)
    m1, m2 = st.columns(2)
    m1.metric("想定月収", yen(total))
    m2.metric("目標月収", yen(plan["goal_monthly"]),
              f"{plan['semi_retire']:.1f}% 達成")
    if not plan["has_income_actuals"]:
        st.caption("事業・労働収入は実績が未記録のため目標値で計算している。下で実績を入れると実測に切り替わる。")

    st.subheader("実績（直近12か月の月平均）")
    b1, b2 = st.columns(2)
    business_avg = inc.recent_average(income_rows, inc.BUSINESS)
    labor_avg = inc.recent_average(income_rows, inc.LABOR)
    b1.metric("事業", yen(business_avg),
              f"{inc.progress(business_avg, goals['goal_business_monthly']):.0f}% 達成")
    b2.metric("労働収入", yen(labor_avg),
              f"{inc.progress(labor_avg, goals['goal_labor_monthly']):.0f}% 達成")
    st.caption("記録の無い月は0として平均する（稼働した月だけの平均だと実力を過大評価するため）。")

    if income_rows:
        monthly = inc.by_month(income_rows)
        trend = pd.DataFrame({
            "月": list(monthly), "合計": [round(v) for v in monthly.values()],
        }).sort_values("月")
        st.plotly_chart(px.bar(trend, x="月", y="合計", title="月別の収入（事業＋労働）"),
                        width="stretch")
    else:
        st.info("収入の記録がまだない。データタブの「収入の記録」で月ごとに入力する。")
