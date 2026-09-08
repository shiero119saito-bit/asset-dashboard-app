"""配当タブ：予定（保有×1株配当）・実績（受取記録）・55歳設計（目標までの逆算）。"""
from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

import dividend as dv
import dividend_history as dh
import portfolio as pf
import simulation as sm

from ui.constants import DIVIDEND_SCOPES, MARKET_LABELS
from ui.format import yen, yen_short
from ui.widgets import pie, show_table


def render(holdings, div_map, months_map, history_rows, goals, plan, cfg) -> None:
    """予定（保有×1株配当）・実績（受取記録）・55歳設計（目標までの逆算）。"""
    view = st.radio("表示", ["予定", "実績", "55歳設計"], horizontal=True, key="div_view")
    if view == "実績":
        _render_dividend_actuals(holdings, div_map, history_rows, cfg)
        return
    if view == "55歳設計":
        _render_dividend_plan(holdings, div_map, history_rows, goals, plan, cfg)
        return
    _render_dividend_forecast(holdings, div_map, months_map, cfg)


@st.fragment
def _render_dividend_forecast(holdings, div_map, months_map, cfg) -> None:
    """予定ビュー：対象（用途）と税込税抜を切り替えて見る。

    fragment＝絞り込みや税表示を切り替えても、再描画はこのビューの中だけで済む。
    """
    f_col, t_col = st.columns([1, 1])
    with f_col:
        scope = st.radio(
            "対象", list(DIVIDEND_SCOPES), horizontal=True, key="div_scope",
            help="用途（purpose）で絞り込む。資産形成（インデックス）や優待の配当を除いた"
                 "「配当目的の資産」だけの利回り・月別CFを見るためのもの",
        )
    with t_col:
        tax_mode = st.radio("税", ["税込", "税抜"], horizontal=True, key="tax_mode")
    pre_tax = tax_mode == "税込"

    div_holdings = pf.filter_by_purpose(holdings, DIVIDEND_SCOPES[scope])
    scope_suffix = "" if not DIVIDEND_SCOPES[scope] else f"・{scope}"
    if not div_holdings:
        st.info(f"「{scope}」に該当する保有がありません。データタブで用途を設定してください。")

    d1, d2, d3, d4 = st.columns(4)
    annual_div = dv.total_annual_dividend(div_holdings, div_map, pre_tax=pre_tax)
    d1.metric(f"年間配当（{tax_mode}{scope_suffix}）", yen(annual_div))
    d2.metric("月平均", yen(annual_div / 12))
    d3.metric("取得額利回り", f"{dv.yield_on_cost(div_holdings, div_map):.2f}%")
    d4.metric("評価額利回り", f"{dv.yield_on_market(div_holdings, div_map):.2f}%")
    st.caption(
        "取得額利回りが評価額利回りを上回る＝買った後に値上がりしている。下回るなら高値づかみ。"
    )
    if DIVIDEND_SCOPES[scope]:
        st.caption(
            f"用途が「{scope}」の保有 {len(pf.group_by_ticker(div_holdings))} 銘柄のみで集計"
            f"（全 {len(pf.group_by_ticker(holdings))} 銘柄中）。利回りの母数（取得額・評価額）も"
            "同じ範囲に絞っているため、配当目的の資産だけの利回りが出る。"
        )
    if not div_map:
        st.info("配当データがありません。holdings.csv の div_per_share を入力するか、時価取得をONにしてください。")

    by_month = dv.dividend_by_month(div_holdings, div_map, months_map, pre_tax=pre_tax)
    month_labels = [f"{m}月" for m in range(1, 13)] + [dv.UNKNOWN_MONTH]
    month_values = [by_month[m] for m in range(1, 13)] + [by_month[dv.UNKNOWN_MONTH]]
    month_df = pd.DataFrame({"月": month_labels, "配当": [round(v) for v in month_values]})
    st.plotly_chart(
        px.bar(month_df, x="月", y="配当", title=f"権利確定月別 配当（{tax_mode}{scope_suffix}）"),
        width="stretch",
    )

    s_col, m_col = st.columns(2)
    by_industry = dv.dividend_by_industry(div_holdings, div_map, pre_tax=pre_tax)
    industry_df = pd.DataFrame(
        {"業種": list(by_industry.keys()), "配当": [round(v) for v in by_industry.values()]}
    ).sort_values("配当", ascending=False)
    s_col.plotly_chart(
        pie(by_industry, "業種", f"業種別 配当（{tax_mode}{scope_suffix}）"), width="stretch",
    )
    show_table(industry_df, s_col, order_key="cols_div_industry", cfg=cfg)

    by_mkt = dv.dividend_by_market(div_holdings, div_map, pre_tax=pre_tax)
    m_col.plotly_chart(
        pie({MARKET_LABELS.get(k, k): v for k, v in by_mkt.items()},
            "市場", f"日米別 配当{scope_suffix}", group_small=False),
        width="stretch",
    )

    st.subheader("配当の源泉（銘柄別）")
    st.caption("上位に偏っていれば、その銘柄の減配が家計に直撃する。")
    per_ticker = {
        group[0].ticker: (group[0].name, sum(dv.holding_dividend(h, div_map, pre_tax) for h in group))
        for group in pf.group_by_ticker(div_holdings).values()
    }
    total_div = sum(v for _, v in per_ticker.values())
    source_df = pd.DataFrame([
        {"銘柄": ticker, "名称": name, "年間配当": round(amount),
         "構成比%": round(amount / total_div * 100, 1) if total_div else 0.0}
        for ticker, (name, amount) in per_ticker.items()
    ]).sort_values("年間配当", ascending=False)
    show_table(source_df.head(15), order_key="cols_div_source", cfg=cfg)


def _render_dividend_plan(holdings, div_map, history_rows, goals, plan, cfg) -> None:
    """「あといくら投資すれば目標配当に届くか」を出す。配当資産の主役はここ。

    評価額ではなく**年間配当**を指標にする。株価が上がっても生活CFが増えるとは限らないため。
    """
    target_age = plan["target_age"]
    years = plan["years"]
    annual_after = plan["dividend_now"]
    goal_annual = goals["goal_dividend_annual"]
    tax_rate = dv.effective_tax_rate(holdings, div_map)
    purchase_yield = dv.yield_at_purchase(holdings)

    st.subheader("いまの配当と目標")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("年間予想配当（税抜）", yen(annual_after))
    c2.metric("月間予想配当（税抜）", yen(annual_after / 12))
    c3.metric(f"{target_age}歳の目標（年・税抜）", yen(goal_annual))
    c4.metric("不足配当（年）", yen(dv.shortfall(goal_annual, annual_after)))

    y1, y2, y3 = st.columns(3)
    y1.metric("平均購入時利回り",
              f"{purchase_yield:.2f}%" if purchase_yield is not None else "—")
    y2.metric("簿価利回り", f"{dv.yield_on_cost(holdings, div_map):.2f}%")
    y3.metric("評価額利回り", f"{dv.yield_on_market(holdings, div_map):.2f}%")
    if purchase_yield is None:
        y1.caption("購入時の1株配当が未入力。データタブで入れた銘柄から集計する")
    st.caption(
        "簿価利回り＝いまの配当 ÷ 投下元本（増配で上がる）。"
        "購入時利回り＝買った時点の配当 ÷ 購入額（新規投資の効率）。"
        f"実効税率 {tax_rate * 100:.2f}%（口座構成から算出）。"
    )

    st.subheader("目標達成に必要な追加投資額")
    assumed_yield = st.number_input(
        "新規購入の想定利回り（%・額面）", value=float(goals["assumed_purchase_yield"]),
        min_value=0.0, step=0.1, format="%.2f", key="plan_purchase_yield",
        help="既定値はデータタブで変更できる。購入時利回りの実測が出ていればそれを目安にする",
    )
    scenarios = dv.growth_scenarios(
        current_annual=annual_after, target_annual=goal_annual, years=years,
        purchase_yield_pct=assumed_yield, tax_rate=tax_rate,
        growth_rates=(goals["scenario_growth_low"], goals["scenario_growth_mid"],
                      goals["scenario_growth_high"]),
    )
    labels = ("保守", "標準", "強気")
    table = pd.DataFrame([
        {
            "シナリオ": f"{label}（増配{row['growth']:.0f}%）",
            f"{target_age}歳の配当（年）": round(row["projected"]),
            "不足": round(row["shortfall"]),
            "必要追加投資": round(row["required"]),
            "月あたり": round(row["required"] / max(1, years) / 12),
        }
        for label, row in zip(labels, scenarios)
    ])
    show_table(table, order_key="cols_scenarios", cfg=cfg)
    st.caption(
        f"必要追加投資 ＝ 不足（税抜）÷（想定利回り {assumed_yield:.2f}% ×(1−実効税率)）。"
        "**追加投資した分の将来増配は織り込んでいない**ため、多めに出る（保守側）。"
        "増配は保証されないので、計画は保守シナリオを基準にすること。"
    )

    measured = dh.growth_rate(
        [r for r in history_rows if not str(r.get("date", "")).startswith(str(date.today().year))]
    )
    if measured is not None:
        st.caption(f"受取実績からの実測増配率は年 {measured:+.1f}%。シナリオの目安にする。")

    st.subheader("配当CFの推移")
    render_dividend_cf(
        current_annual_dividend=dv.total_annual_dividend(holdings, div_map, pre_tax=True),
        purchase_yield=assumed_yield,
        years=years, target_age=target_age, tax_rate=tax_rate,
        growth_default=goals["scenario_growth_mid"],
        target_monthly=goal_annual / 12,
    )

    st.subheader("資産状況（参考）")
    st.caption("配当資産の評価額。**55歳の達成率には使わない**（株価が上がってもCFは増えないため）。")
    dividend_holdings = pf.filter_by_purpose(holdings, ("dividend", "yutai"))
    cost = sum(h.cost_value for h in dividend_holdings)
    value = sum(h.market_value for h in dividend_holdings)
    s1, s2, s3 = st.columns(3)
    s1.metric("配当資産の評価額", yen(value))
    s2.metric("投下元本", yen(cost))
    s3.metric("含み損益", yen(value - cost),
              f"{(value - cost) / cost * 100:+.1f}%" if cost else None)


@st.fragment
def render_dividend_cf(
    current_annual_dividend: float, purchase_yield: float, years: int, target_age: int,
    tax_rate: float, growth_default: float = sm.DEFAULT_DIVIDEND_GROWTH,
    target_monthly: float = sm.TARGET_CF_MONTHLY_MIN,
) -> None:
    """配当CFの推移：既存分は増配で伸び、新規買付分が利回り分の配当を上乗せする。

    fragment＝積立額・増配率・配分割合を動かしても再計算はこのグラフだけで済む。
    呼び出し元（`_render_dividend_plan`）は fragment にしない（入れ子は不可）。

    purchase_yield は**これから買う分の利回り**（購入時利回り）。計算式が
    「その年の買い付け額 × 利回り」なので、評価額利回りを入れてはいけない
    （値上がり後の低い利回りを新規購入に当てることになり、将来配当を過小評価する）。

    tax_rate はいまの保有の口座構成から出した実効税率。一律 20.315% で見積もると、
    NISA 分が非課税である実態を反映できず手取りを過小評価する。
    """
    c1, c2 = st.columns(2)
    monthly = c1.number_input(
        "毎月の積立額", value=sm.DEFAULT_MONTHLY_CONTRIBUTION, step=10_000.0,
        format="%.0f", key="cf_monthly",
    )
    dividend_growth = c2.number_input(
        "想定増配率（%/年）", value=float(growth_default), step=0.5, format="%.1f"
    )
    dividend_yield = purchase_yield
    st.caption(
        f"新規購入の想定利回り {dividend_yield:.2f}%（上の設定値）を、その年の買い付け額に掛ける。"
        "既存の保有分には掛からず、増配率だけで伸びる。"
    )

    income_ratio = st.slider(
        "積立のうち配当資産へ回す割合（%）",
        min_value=0, max_value=100,
        value=int(sum(pf.TARGET_ALLOCATION[ac] for ac in ("us_dividend", "jp_dividend", "reit"))),
        help="既定は目標AAの高配当系合計（米国高配当20＋日本高配当15＋REIT5＝40%）",
    )
    tax_mode = st.radio(
        "表示", ["税抜（手取り）", "税込"], horizontal=True, key="cf_tax_mode",
        help="生活費に充てられる額で見るため既定は税抜",
    )
    pre_tax = tax_mode == "税込"
    st.caption(
        f"税抜は現在の口座構成から算出した実効税率 {tax_rate * 100:.2f}% で換算している"
        "（NISA比率が上がるほど下がる）。将来の口座構成の変化は織り込まない。"
    )

    points = sm.project_dividend_cf(
        current_annual_dividend=current_annual_dividend,
        monthly=monthly,
        years=years,
        dividend_yield=dividend_yield,
        dividend_growth=dividend_growth,
        income_ratio=income_ratio / 100.0,
        tax_rate=tax_rate,
    )
    last = points[-1]
    last_monthly = last.monthly_pre_tax if pre_tax else last.monthly_after_tax
    # 到達判定は**設定した配当目標**で行う（以前は月6万のハードコードだった）
    reach_year = sm.first_year_reaching(points, target_monthly, pre_tax=pre_tax)
    goal_label = f"月{yen_short(target_monthly)}到達"

    m1, m2, m3 = st.columns(3)
    m1.metric(f"{target_age}歳時点の月額配当（{tax_mode}）", yen(last_monthly))
    m2.metric("年間配当", yen(last.annual_pre_tax if pre_tax else last.annual_after_tax))
    if reach_year is None:
        m3.metric(goal_label, "期間内に未到達")
    else:
        m3.metric(goal_label, f"{reach_year}年後（{target_age - years + reach_year}歳）")

    values = [p.monthly_pre_tax if pre_tax else p.monthly_after_tax for p in points]
    df = pd.DataFrame({"経過年": [p.year for p in points], "月額配当": values})
    fig = px.line(df, x="経過年", y="月額配当", title=f"月額配当CFの推移（{tax_mode}）")
    fig.add_hline(
        y=target_monthly, line_dash="dash", line_color="#b8871f",
        annotation_text=f"目標 月{yen_short(target_monthly)}", annotation_position="top left",
    )
    st.plotly_chart(fig, width="stretch")


def _render_dividend_actuals(holdings, div_map, history_rows, cfg) -> None:
    """受取実績。ここだけが「本当にいくら入ったか」を示す。"""
    if not history_rows:
        st.info(
            "受取配当の記録がまだない。データタブの「配当実績」で入力するか、"
            "同じ列を持つCSVを取り込むと、年別推移・実測増配率・予定比が出る。"
        )
        return

    this_year = str(date.today().year)
    by_year = dh.by_year(history_rows)
    planned_after_tax = dv.total_annual_dividend(holdings, div_map, pre_tax=False)
    progress = dh.progress_against_plan(history_rows, planned_after_tax, this_year)
    # 当年は途中までしか受け取っていないため、増配率は当年を除いて算出する
    closed_years = [r for r in history_rows if not str(r.get("date", "")).startswith(this_year)]
    growth = dh.growth_rate(closed_years)

    a1, a2, a3, a4 = st.columns(4)
    a1.metric(f"{this_year}年の受取（手取り）", yen(by_year.get(this_year, 0.0)))
    a2.metric("累計受取", yen(sum(by_year.values())))
    a3.metric("予定に対する到達率", f"{progress:.1f}%" if progress is not None else "—")
    a4.metric("実測増配率（年）", f"{growth:+.1f}%" if growth is not None else "—")
    if growth is None:
        st.caption("増配率は当年を除いた2年分以上の記録がたまると出る（途中の年を混ぜると過小評価になる）。")

    year_df = pd.DataFrame({
        "年": list(by_year), "受取（手取り）": [round(v) for v in by_year.values()],
    }).sort_values("年")
    st.plotly_chart(
        px.bar(year_df, x="年", y="受取（手取り）", title="年間受取配当の推移（実績）"),
        width="stretch",
    )

    left, right = st.columns(2)
    by_month = dh.by_month(history_rows, year=this_year)
    month_df = pd.DataFrame({
        "月": list(by_month), "受取": [round(v) for v in by_month.values()],
    }).sort_values("月")
    left.plotly_chart(
        px.bar(month_df, x="月", y="受取", title=f"{this_year}年 月別受取"), width="stretch",
    )

    industry_by_ticker = {h.ticker: h.industry for h in holdings}
    by_industry = dh.by_industry(history_rows, industry_by_ticker, year=this_year)
    right.plotly_chart(
        pie(by_industry, "業種", f"{this_year}年 業種別受取"), width="stretch",
    )

    st.subheader("受取明細")
    show_table(pd.DataFrame(dh.sort_rows(history_rows)), order_key="cols_history", cfg=cfg)
