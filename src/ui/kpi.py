"""55歳設計の共通計算と、全タブの上に置くKPI帯。"""
from __future__ import annotations

from datetime import date

import streamlit as st

import cash as ca
import dataio
import dividend as dv
import income as inc
import portfolio as pf
import simulation as sm
import snapshots as sn

from ui.format import delta_text, tone, yen, yen_short
from ui.widgets import kpi_cell


def plan_numbers(holdings, div_map, cash_rows, income_rows, goals, birth_date) -> dict:
    """55歳設計の共通計算。KPI帯・インデックス・収入計画の3か所で同じ値を使う。

    配当は**保守シナリオ（増配0%）**を既定にする。増配は保証されないため、
    生活設計の基準はここに置く（強気シナリオは配当タブで別途見せる）。
    """
    today = date.today()
    target_age = int(goals["target_age"])
    years = sm.years_until_age(birth_date, today, target_age)

    index_now = sum(h.market_value for h in holdings if h.asset_class == "index")
    index_points = sm.project_accumulation(
        index_now, goals["assumed_index_monthly"], years, goals["assumed_index_return"]
    )
    index_future = index_points[-1].value

    dividend_now = dv.total_annual_dividend(holdings, div_map, pre_tax=False)
    dividend_future = dv.project_dividend(dividend_now, goals["scenario_growth_low"], years)

    # 事業・労働収入は実績があればその直近平均、無ければ目標値を置く
    business_actual = inc.recent_average(income_rows, inc.BUSINESS)
    labor_actual = inc.recent_average(income_rows, inc.LABOR)
    business = business_actual or goals["goal_business_monthly"]
    labor = labor_actual or goals["goal_labor_monthly"]

    monthly_income = (
        dividend_future / 12 + goals["goal_withdrawal_monthly"] + business + labor
    )
    # 月間の配当目標は**年間目標を12で割って導く**。別々に保存された値を混ぜると、
    # 達成率（年間ベース）と想定月収（月間ベース）が食い違う
    goal_monthly = (
        goals["goal_dividend_annual"] / 12 + goals["goal_withdrawal_monthly"]
        + goals["goal_business_monthly"] + goals["goal_labor_monthly"]
    )
    return {
        "target_age": target_age,
        "years": years,
        "current_age": sm.age_at(birth_date, today),
        "index_now": index_now,
        "index_future": index_future,
        "index_points": index_points,
        "dividend_now": dividend_now,
        "dividend_future": dividend_future,
        "business": business,
        "labor": labor,
        "has_income_actuals": bool(business_actual or labor_actual),
        "monthly_income": monthly_income,
        "goal_monthly": goal_monthly,
        "semi_retire": dataio.goal_progress(monthly_income, goal_monthly),
        "total_assets": ca.net_worth(pf.total_market(holdings), cash_rows),
    }


def render_kpi_bar(holdings, div_map, cash_rows, snapshot_rows, goals, plan) -> None:
    """全タブ共通のKPI。**55歳のCF設計に効く数字だけ**を置き、細部は各タブへ送る。

    3項目 × 2段を1つの枠に収める。上段＝いまの資産、下段＝55歳の生活設計。
    """
    market = pf.total_market(holdings)
    annual_after = plan["dividend_now"]
    dividend_progress = dataio.goal_progress(annual_after, goals["goal_dividend_annual"])
    change = sn.change_from_previous(snapshot_rows, "net_worth")

    with st.container(border=True):
        a1, a2, a3 = st.columns(3)
        kpi_cell(
            a1, "総資産", yen(plan["total_assets"]),
            side=delta_text(change) or "", tone=tone(change[0]) if change else "",
            subs=(
                f"現金：{yen_short(ca.total(cash_rows))}（{ca.cash_ratio(market, cash_rows):.1f}%）",
                f"運用：{yen_short(market)}（{ca.invested_ratio(market, cash_rows):.1f}%）",
            ),
        )
        kpi_cell(
            a2, "年間予想配当（税抜）", yen(annual_after),
            side=f"月 {yen_short(annual_after / 12)}",
            subs=(
                f"税込：{yen_short(dv.total_annual_dividend(holdings, div_map, pre_tax=True))}",
                f"簿価利回り：{dv.yield_on_cost(holdings, div_map):.2f}%",
            ), divider=True,
        )
        kpi_cell(
            a3, "配当目標達成率", f"{dividend_progress:.1f}%",
            side=f"{yen_short(annual_after)} / {yen_short(goals['goal_dividend_annual'])}",
            progress=dividend_progress / 100.0,
            subs=(f"不足：{yen_short(dv.shortfall(goals['goal_dividend_annual'], annual_after))}（年・税抜）",),
            divider=True,
        )

        st.markdown('<hr class="kpi-hr">', unsafe_allow_html=True)
        b1, b2, b3 = st.columns(3)
        kpi_cell(
            b1, "インデックス", yen(plan["index_now"]),
            side=f"→ {plan['target_age']}歳 {yen_short(plan['index_future'])}",
            subs=(
                f"積立：月 {yen_short(goals['assumed_index_monthly'])}"
                f"・想定年率 {goals['assumed_index_return']:.1f}%",
                f"残り {plan['years']}年（現在 {plan['current_age']}歳）",
            ),
        )
        kpi_cell(
            b2, f"{plan['target_age']}歳 想定月収", yen(plan["monthly_income"]),
            side=f"目標 {yen_short(plan['goal_monthly'])}",
            subs=(
                f"配当 {yen_short(plan['dividend_future'] / 12)}"
                f"・取崩 {yen_short(goals['goal_withdrawal_monthly'])}",
                f"事業 {yen_short(plan['business'])}・労働 {yen_short(plan['labor'])}"
                + ("" if plan["has_income_actuals"] else "（目標値）"),
            ), divider=True,
        )
        kpi_cell(
            b3, "セミリタイア達成率", f"{plan['semi_retire']:.1f}%",
            side=f"{yen_short(plan['monthly_income'])} / {yen_short(plan['goal_monthly'])}",
            progress=plan["semi_retire"] / 100.0,
            subs=("配当は増配0%（保守）で計算",), divider=True,
        )
