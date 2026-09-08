"""FIRE STATION（資産管理ダッシュボード・Streamlit エントリ）。

実行: streamlit run 400_Asset-management/src/app.py
データソース優先順：アップロードCSV → st.secrets[holdings] → data/holdings.csv → sample

描画は `ui/` パッケージに置く（タブ単位のモジュール）。ここは
「読み込む → 集計に渡す → タブへ配る」だけを持つ。

**画面は外部から取得しない**。時価・配当・権利確定月はすべて holdings.csv の
保存値を読む（書き込むのは scripts/refresh_prices.py＝PC・GitHub Actions）。
"""
from __future__ import annotations

import streamlit as st

import portfolio as pf
import pricing_update as pu
import cash as ca
import dividend_history as dh
import income as inc
import snapshots as sn

from ui import (
    tab_data,
    tab_dividend,
    tab_income,
    tab_index,
    tab_overview,
    tab_performance,
    tab_portfolio,
)
from ui.appdata import load_goals, load_rows, load_side_csv, load_view_orders, storage_config
from ui.constants import (
    CASH_CSV,
    CASH_PATH,
    CASH_STATE,
    HISTORY_CSV,
    HISTORY_PATH,
    HISTORY_STATE,
    INCOME_CSV,
    INCOME_PATH,
    INCOME_STATE,
    SNAPSHOTS_CSV,
    SNAPSHOTS_PATH,
    SNAPSHOT_STATE,
    VIEW_ORDERS_STATE,
)
from ui.datasource import merge_uploaded, render_birth_date_input, render_data_freshness
from ui.kpi import plan_numbers, render_kpi_bar
from ui.style import APP_STYLE


def main() -> None:
    # page_title はブラウザタブとスマホのホーム画面アイコン名になる。10〜12文字で
    # 切られるため、副題は入れず名前だけにする
    st.set_page_config(page_title="FIRE STATION", page_icon="🔥", layout="wide")
    st.markdown(APP_STYLE, unsafe_allow_html=True)
    st.markdown(
        '<div class="app-title"><span class="name">FIRE STATION</span>'
        '<span class="sub">資産管理ダッシュボード</span></div>',
        unsafe_allow_html=True,
    )

    cfg = storage_config()
    if VIEW_ORDERS_STATE not in st.session_state:
        # 保存済みの列順は1セッションに1回だけ読む（再実行のたびに API を叩かない）
        st.session_state[VIEW_ORDERS_STATE] = load_view_orders(cfg)

    st.sidebar.subheader("データ")
    uploaded = st.sidebar.file_uploader("保有CSVをアップロード（任意）", type="csv")
    uploaded_text = uploaded.getvalue().decode("utf-8-sig") if uploaded is not None else None
    merge_mode = "マージ"
    if uploaded_text:
        merge_mode = st.sidebar.radio(
            "取込方法", ["マージ", "置換"], horizontal=True, key="import_mode",
            help="マージ＝既存の分類（用途・資産クラス）を保ち、株数と取得単価だけ更新する",
        )

    try:
        rows, src, sha = load_rows(None if merge_mode == "マージ" else uploaded_text)
        if uploaded_text and merge_mode == "マージ":
            rows, src = merge_uploaded(rows, uploaded_text, src)
    except ValueError as e:
        st.error(f"CSVの読み込みに失敗しました：{e}")
        st.stop()

    st.sidebar.caption(f"データソース：{src}")
    if "サンプル" in src:
        st.warning("サンプルデータを表示中です。実データはCSVアップロード、または data/holdings.csv で表示されます。")
    if uploaded_text:
        st.info(
            f"アップロードしたCSVを反映中（{merge_mode}）。"
            + ("下の「保有データの編集」で保存すると確定する。" if cfg else "確定するには編集欄からCSVを保存すること。")
        )

    birth_date = render_birth_date_input(cfg)

    # 時価は price 列（保存値）→ 取得単価の順にフォールバックする。
    # ライブ取得を渡さないので price_map は空でよい（build_holdings が行から読む）
    holdings = pf.build_holdings(rows, {})
    render_data_freshness(rows, holdings, cfg)

    # 配当は手入力（div_per_share）→ 自動取得（div_annual）の順。権利確定月は div_months
    div_map = pu.dividend_map(rows)
    months_map = pu.dividend_months_map(rows)

    # --- 付随データ（現金・スナップショット・配当実績）---
    cash_rows, _ = load_side_csv(CASH_STATE, CASH_PATH, CASH_CSV, ca.parse_csv)
    snapshot_rows, _ = load_side_csv(SNAPSHOT_STATE, SNAPSHOTS_PATH, SNAPSHOTS_CSV, sn.parse_csv)
    history_rows, _ = load_side_csv(HISTORY_STATE, HISTORY_PATH, HISTORY_CSV, dh.parse_csv)
    goals = load_goals(cfg)

    income_rows, _ = load_side_csv(INCOME_STATE, INCOME_PATH, INCOME_CSV, inc.parse_csv)
    plan = plan_numbers(holdings, div_map, cash_rows, income_rows, goals, birth_date)

    # --- 共通KPI（タブの上に固定。どのタブにいても現在地が分かる）---
    render_kpi_bar(holdings, div_map, cash_rows, snapshot_rows, goals, plan)

    tabs = st.tabs(
        ["概要", "配当", "インデックス", "収入計画", "資産・成績", "ポートフォリオ", "データ"]
    )
    with tabs[0]:
        tab_overview.render(holdings, div_map, cash_rows, snapshot_rows, goals, cfg)
    with tabs[1]:
        tab_dividend.render(holdings, div_map, months_map, history_rows, goals, plan, cfg)
    with tabs[2]:
        tab_index.render(holdings, goals, plan, cfg)
    with tabs[3]:
        tab_income.render(income_rows, goals, plan, cfg)
    with tabs[4]:
        tab_performance.render(holdings, snapshot_rows, history_rows, cfg)
    with tabs[5]:
        tab_portfolio.render(holdings, div_map, cash_rows, cfg)
    with tabs[6]:
        tab_data.render(rows, sha, cfg, cash_rows, history_rows, income_rows, holdings, div_map)


if __name__ == "__main__":
    main()
