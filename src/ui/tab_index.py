"""インデックスタブ：評価額で管理し、55歳以降は取り崩してCFに変える。"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

import portfolio as pf
import simulation as sm

from ui.cache import cached_price_history
from ui.constants import BACKTEST_BASE_PROXIES, BACKTEST_INDEX_CHOICES
from ui.format import yen
from ui.widgets import show_table


def render(holdings, goals, plan, cfg) -> None:
    """インデックスは評価額で管理し、55歳以降は取り崩してCFに変える。"""
    target_age = plan["target_age"]
    years = plan["years"]

    st.subheader("いまと将来")
    c1, c2, c3 = st.columns(3)
    c1.metric("現在の評価額", yen(plan["index_now"]))
    c2.metric(f"{target_age}歳の予想額", yen(plan["index_future"]))
    c3.metric("残り期間", f"{years}年")
    st.caption(
        f"毎月 {yen(goals['assumed_index_monthly'])} を年率 {goals['assumed_index_return']:.1f}% で"
        "積み立てた場合。前提値はデータタブで変更できる。予測や推奨ではない。"
    )

    points = plan["index_points"]
    df = pd.DataFrame({
        "経過年": [p.year for p in points] * 2,
        "金額": [p.principal for p in points] + [p.value for p in points],
        "区分": ["投下元本"] * len(points) + ["評価額"] * len(points),
    })
    st.plotly_chart(
        px.line(df, x="経過年", y="金額", color="区分", title=f"{target_age}歳までの推移"),
        width="stretch",
    )

    ages = [target_age, target_age + 5, target_age + 10]
    horizon = pd.DataFrame([
        {"年齢": f"{age}歳",
         "予想評価額": round(sm.project_accumulation(
             plan["index_now"], goals["assumed_index_monthly"],
             max(0, age - plan["current_age"]), goals["assumed_index_return"])[-1].value)}
        for age in ages
    ])
    show_table(horizon, order_key="cols_index_horizon", cfg=cfg)

    _render_withdrawal(goals, plan)

    st.subheader("バックテスト")
    _render_backtest()


@st.fragment
def _render_withdrawal(goals, plan) -> None:
    """取り崩しの試算。fragment＝金額・年率を動かしてもこのセクションだけ再計算する。"""
    target_age = plan["target_age"]
    st.subheader("取り崩し")
    st.caption("55歳以降、毎月いくら引き出すと何歳まで持つか。定額取り崩しは相場が悪い年に負担が重くなる。")
    w1, w2 = st.columns(2)
    monthly_withdrawal = w1.number_input(
        "毎月の取り崩し額", value=float(goals["goal_withdrawal_monthly"]),
        min_value=0.0, step=10_000.0, format="%.0f", key="withdrawal_monthly",
    )
    withdrawal_return = w2.number_input(
        "取り崩し期の想定年率（%）", value=float(goals["assumed_index_return"]),
        step=0.5, format="%.1f", key="withdrawal_return",
    )
    depleted = sm.depletion_age(
        target_age, plan["index_future"], withdrawal_return, monthly_withdrawal
    )
    d1, d2 = st.columns(2)
    d1.metric("取り崩し開始時の残高", yen(plan["index_future"]))
    d2.metric("枯渇年齢", f"{depleted}歳" if depleted else "60年後も残る")

    wp = sm.project_withdrawal(plan["index_future"], withdrawal_return, monthly_withdrawal, 40)
    if wp:
        wdf = pd.DataFrame({
            "年齢": [target_age + p.year for p in wp],
            "残高": [round(p.balance) for p in wp],
        })
        st.plotly_chart(px.line(wdf, x="年齢", y="残高", title="取り崩し後の残高"), width="stretch")


@st.fragment
def _render_backtest() -> None:
    """バックテスト：目標AAで過去の積立を再現し、インデックス投資先を比較する。

    fragment＝期間・積立額・比較対象を選び直しても、他のタブは再実行されない。
    """
    st.caption(
        "保有銘柄すべての履歴取得は重いため、資産クラス代表銘柄で代替する。"
        "固定枠："
        + " / ".join(
            f"{pf.ASSET_CLASS_LABELS[ac]}({pf.TARGET_ALLOCATION[ac]:.0f}%)={name}"
            for ac, (_, name) in BACKTEST_BASE_PROXIES.items()
        )
    )

    c1, c2 = st.columns(2)
    years = c1.selectbox("期間（年）", [3, 5, 8, 10], index=1)
    monthly = c2.number_input(
        "毎月の積立額", value=sm.DEFAULT_MONTHLY_CONTRIBUTION, step=10_000.0,
        format="%.0f", key="bt_monthly",
    )
    selected = st.multiselect(
        f"インデックス枠（{pf.TARGET_ALLOCATION['index']:.0f}%）に何を積み立てた場合を見るか",
        list(BACKTEST_INDEX_CHOICES),
        default=list(BACKTEST_INDEX_CHOICES),
        help="複数選ぶと、他の枠は同じまま インデックス枠だけを入れ替えて比較する",
    )

    if not st.button("バックテストを実行", type="primary"):
        st.caption("※ 価格履歴の取得を伴うためボタンで明示実行する。")
        return
    if not selected:
        st.warning("インデックス枠の投資先を1つ以上選ぶこと。")
        return

    base_tickers = [t for t, _ in BACKTEST_BASE_PROXIES.values()]
    index_tickers = [BACKTEST_INDEX_CHOICES[label][0] for label in selected]
    all_names = {t: n for t, n in BACKTEST_BASE_PROXIES.values()}
    all_names.update({t: n for t, n in BACKTEST_INDEX_CHOICES.values()})

    history = cached_price_history(tuple(base_tickers + index_tickers), years)
    if not history:
        st.warning("価格履歴を取得できませんでした（オフライン/未導入）。")
        return

    missing = [t for t in base_tickers + index_tickers if t not in history]
    if missing:
        st.caption(
            "履歴を取得できず除外した銘柄："
            + "、".join(f"{all_names.get(t, t)}（{t}）" for t in missing)
        )

    # 分割が未調整で価格が不連続な銘柄は結果を無意味にするため除外する
    discontinuous = sm.find_discontinuous(history)
    if discontinuous:
        st.warning(
            "価格履歴が不連続なため除外した銘柄："
            + "、".join(f"{all_names.get(t, t)}（{t}）" for t in discontinuous)
            + "。yfinance が日本ETFの株式分割を調整しないことがあり、"
            "そのままでは見かけ上の暴落・暴騰として計算されてしまうため。"
        )
        history = {t: s for t, s in history.items() if t not in discontinuous}

    base_weights = {
        t: pf.TARGET_ALLOCATION[ac]
        for ac, (t, _) in BACKTEST_BASE_PROXIES.items()
        if t in history
    }

    results: list[tuple[str, sm.BacktestResult]] = []
    for label in selected:
        index_ticker = BACKTEST_INDEX_CHOICES[label][0]
        weights = dict(base_weights)
        if index_ticker in history:
            weights[index_ticker] = pf.TARGET_ALLOCATION["index"]
        if not weights:
            continue
        sub_history = {t: s for t, s in history.items() if t in weights}
        result = sm.backtest_dca(sub_history, weights, monthly)
        if result.series:
            results.append((label, result))

    if not results:
        st.error("使える価格履歴が残らなかったためバックテストできない。")
        return

    # 実際に計算に入った配分を示す（欠けたクラスがあれば明示する）
    used_weight = sum(base_weights.values())
    covered = pf.TARGET_ALLOCATION["index"] if any(
        BACKTEST_INDEX_CHOICES[label][0] in history for label in selected
    ) else 0.0
    total_weight = used_weight + covered
    if total_weight < 100:
        st.caption(
            f"計算に入った配分は目標AAの{total_weight:.0f}%相当。"
            "欠けたクラスがある点に注意。"
        )

    invested = results[0][1].invested
    st.metric("投下元本（共通）", yen(invested))

    cols = st.columns(len(results))
    for col, (label, result) in zip(cols, results):
        col.markdown(f"**{label}**")
        col.metric("最終評価額", yen(result.final_value))
        col.metric("リターン", f"{result.return_rate:+.1f}%")
        col.metric("最大含み損率", f"{result.worst_unrealized_rate:.1f}%")

    start, end = results[0][1].series[0][0], results[0][1].series[-1][0]
    st.caption(
        f"実際に使えた期間：{start} 〜 {end}（{results[0][1].months}ヶ月）。"
        "代表銘柄の上場時期により選択期間より短くなることがある。"
    )
    st.caption(
        "価格は yfinance の調整後終値（配当・分割を遡及調整）。調整の精度は銘柄により差があり、"
        "為替は考慮しない（米国銘柄はドル建てのまま比率計算）。結果は参考値として扱うこと。"
    )

    # 投下元本は共通なので1本だけ、評価額はシナリオごとに重ねる
    dates = [d for d, _, _ in results[0][1].series]
    frames = [
        pd.DataFrame({
            "日付": dates,
            "金額": [p for _, p, _ in results[0][1].series],
            "区分": "投下元本",
        })
    ]
    for label, result in results:
        frames.append(pd.DataFrame({
            "日付": [d for d, _, _ in result.series],
            "金額": [v for _, _, v in result.series],
            "区分": label,
        }))
    fig = px.line(pd.concat(frames), x="日付", y="金額", color="区分", title="積立バックテスト")
    st.plotly_chart(fig, width="stretch")
