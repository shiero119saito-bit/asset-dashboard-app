"""データタブ：編集・取込・設定をまとめる。普段は開かないタブ。

各エディタは `@st.fragment`。Streamlit はウィジェット1つ動かすと全スクリプトを
再実行するため、表を1マス直すたびに全タブが組み直されていた。フラグメントにすると
再実行はその関数の中だけで済む。

**保存後の再実行だけは `scope="app"`**。KPI帯（総資産・達成率）はタブより前に
描画済みで、既定の `scope="fragment"` では古い数字が残ってしまう。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

import cash as ca
import dataio
import dividend_history as dh
import income as inc
import portfolio as pf
import snapshots as sn
import storage as sg

from ui.appdata import load_goals, load_side_csv, save_goals, save_side_csv
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
    PURPOSE_LABELS_BY_VALUE,
    SNAPSHOTS_CSV,
    SNAPSHOTS_PATH,
    SNAPSHOT_STATE,
)
from ui.format import yen


def render(rows, sha, cfg, cash_rows, history_rows, income_rows, holdings, div_map) -> None:
    """編集・取込・設定をまとめる。普段は開かないタブ。"""
    _render_holdings_editor(rows, sha, cfg)
    _render_cash_editor(cash_rows)
    _render_income_editor(income_rows)
    _render_goal_editor(cfg)
    _render_history_editor(history_rows)
    _render_snapshot_button(holdings, div_map, cash_rows)


@st.fragment
def _render_holdings_editor(rows: list[dict], sha: str | None, cfg) -> None:
    """保有データを画面で直接編集し、保存先へ書き戻す。

    普段の買い増し・分類の修正はここで完結させる（CSV取込は初期と一括更新のみ）。
    保存先が未設定の環境ではCSVダウンロードに切り替え、編集自体は行えるようにする。
    """
    st.subheader("保有データの編集")
    st.caption(
        "株数・取得単価の修正、銘柄の追加・削除ができる。"
        "行を増やすには表の最下部に入力する。"
    )

    editable = pd.DataFrame(
        [{col: dataio._cell(row.get(col)) for col in dataio.HOLDINGS_COLUMNS} for row in rows]
    )
    # 数値列は数値として編集させる（文字列のままだと計算に使えない値が入りうる）
    for col in ("shares", "cost_per_share", "div_per_share", "div_at_purchase", "price",
                "market_cap"):
        editable[col] = pd.to_numeric(editable[col], errors="coerce")

    edited = st.data_editor(
        editable,
        width="stretch",
        hide_index=True,
        num_rows="dynamic",
        key="holdings_editor",
        column_config={
            "ticker": st.column_config.TextColumn("銘柄コード", required=True),
            "name": st.column_config.TextColumn("名称", required=True),
            "asset_class": st.column_config.SelectboxColumn(
                "資産クラス", options=list(pf.ASSET_CLASSES), required=True
            ),
            # 桁区切りは printf の "," 指定（Streamlit 1.30+ が sprintf-js を通す）。
            # 株数・取得単価・時価は桁が大きく、区切りが無いと読み違える
            "shares": st.column_config.NumberColumn("株数", min_value=0.0, format="%,d", step=1),
            "cost_per_share": st.column_config.NumberColumn("取得単価", min_value=0.0, format="%,.2f"),
            "sector": st.column_config.TextColumn("商品種別"),
            "industry": st.column_config.SelectboxColumn(
                "業種", options=[""] + list(pf.INDUSTRIES),
                help="東証33業種。ETF・投信/REITは末尾の区分を選ぶ。空欄は未分類として集計",
            ),
            "market": st.column_config.SelectboxColumn("上場市場", options=["jp", "us"]),
            "region": st.column_config.SelectboxColumn(
                "投資対象地域", options=[""] + list(pf.REGIONS),
                help="投信・ETFの中身で選ぶ（オルカン＝全世界／S&P500＝米国）。"
                     "個別株は空欄でよい＝上場市場から自動で決まる",
            ),
            "market_cap": st.column_config.NumberColumn(
                "時価総額", min_value=0.0, format="%,d",
                help="週1の自動更新が書く（円建て）。ETF・投信は取得できないため空欄",
            ),
            "div_per_share": st.column_config.NumberColumn("1株配当", min_value=0.0, format="%,.2f"),
            "div_at_purchase": st.column_config.NumberColumn(
                "購入時1株配当", min_value=0.0, format="%,.2f",
                help="買った時点の年1株配当。購入時利回り（新規投資の効率）の分子。未入力は集計対象外",
            ),
            "purpose": st.column_config.SelectboxColumn(
                "用途", options=[""] + list(PURPOSE_LABELS_BY_VALUE),
                help="保有目的。dividend=配当収入 / growth=資産形成（インデックス）/ yutai=優待",
            ),
            "source": st.column_config.TextColumn("証券会社"),
            "account": st.column_config.SelectboxColumn(
                "口座", options=list(dataio.ACCOUNTS),
                help="特定以外は配当の国内課税が非課税。空欄は特定として扱う（課税＝安全側）",
            ),
            "price": st.column_config.NumberColumn("保存時価", min_value=0.0, format="%,.2f"),
            "price_asof": st.column_config.TextColumn("時価の日付"),
            # 投資信託の基準価額取得に使う。上場銘柄では空欄のままでよい
            "isin": st.column_config.TextColumn("ISIN（投信）"),
            "assoc_fund_cd": st.column_config.TextColumn("協会コード（投信）"),
        },
    )

    edited_rows = edited.to_dict("records")
    csv_text = dataio.serialize_holdings_csv(edited_rows)

    left, right = st.columns([1, 2])
    if cfg is None:
        left.download_button(
            "編集内容をCSVで保存", data=csv_text.encode("utf-8"),
            file_name="holdings.csv", mime="text/csv",
        )
        right.caption(
            "保存先が未設定のため、編集結果はダウンロードして手元のCSVを置き換えること。"
            "自動保存の設定手順は docs/04_deploy.md を参照。"
        )
        return

    if left.button("保存", type="primary", key="save_holdings"):
        ok, message = sg.save(cfg, csv_text, sha, f"update holdings ({date.today().isoformat()})")
        if ok:
            st.cache_data.clear()  # 銘柄構成が変わるため取得済みの履歴等を捨てる
            st.success(message)
            st.rerun(scope="app")
        else:
            st.error(message)
    right.caption(f"保存先：{cfg.owner}/{cfg.repo}/{cfg.path}（{cfg.branch}）")
    if right.button("保存先への接続を確認", key="check_storage"):
        ok, message = sg.check(cfg)
        (right.success if ok else right.error)(message)


@st.fragment
def _render_cash_editor(cash_rows) -> None:
    """現金・預金の残高。ここが入ると総資産・現金比率・運用比率が出る。"""
    st.subheader("現金・預金")
    st.caption("待機資金の残高を口座ごとに入力する。証券口座の外にあるため自動取得はできない。")
    editable = pd.DataFrame(cash_rows or [], columns=list(ca.CASH_COLUMNS))
    editable["amount"] = pd.to_numeric(editable["amount"], errors="coerce")
    edited = st.data_editor(
        editable, width="stretch", hide_index=True, num_rows="dynamic", key="cash_editor",
        column_config={
            "name": st.column_config.TextColumn("口座・名称", required=True),
            "amount": st.column_config.NumberColumn("残高", min_value=0.0, format="%,d"),
            "note": st.column_config.TextColumn("メモ"),
        },
    )
    text = ca.serialize_csv(edited.to_dict("records"))
    left, right = st.columns([1, 2])
    if left.button("現金を保存", key="save_cash"):
        ok, message = save_side_csv(CASH_STATE, CASH_PATH, CASH_CSV, text, ca.parse_csv,
                                    "update cash")
        (st.success if ok else st.error)(message)
        if ok:
            st.rerun(scope="app")
    right.caption(f"合計 {yen(ca.total(edited.to_dict('records')))}")


@st.fragment
def _render_income_editor(income_rows) -> None:
    """事業・労働収入の実績。月ごとに1行入れると55歳想定月収が実測に変わる。"""
    st.subheader("収入の記録")
    st.caption(
        "事業（副業）と労働収入（パート）の**手取り**を月ごとに入力する。"
        "配当の目標が税抜なので基準を揃える。同じ月・同じ区分は1行にまとめる。"
    )
    editable = pd.DataFrame(income_rows or [], columns=list(inc.INCOME_COLUMNS))
    editable["amount"] = pd.to_numeric(editable["amount"], errors="coerce")
    edited = st.data_editor(
        editable, width="stretch", hide_index=True, num_rows="dynamic", key="income_editor",
        column_config={
            "month": st.column_config.TextColumn("月", help="YYYY-MM"),
            "category": st.column_config.SelectboxColumn("区分", options=list(inc.CATEGORIES)),
            "amount": st.column_config.NumberColumn("金額（手取り）", min_value=0.0, format="%,d"),
            "note": st.column_config.TextColumn("メモ"),
        },
    )
    if st.button("収入を保存", key="save_income"):
        text = inc.serialize_csv(edited.to_dict("records"))
        ok, message = save_side_csv(INCOME_STATE, INCOME_PATH, INCOME_CSV, text, inc.parse_csv,
                                    "update income")
        (st.success if ok else st.error)(message)
        if ok:
            st.rerun(scope="app")


@st.fragment
def _render_goal_editor(cfg) -> None:
    """目標と前提値の設定。KPIとシミュレーションの分母・係数はすべてここで決まる。"""
    st.subheader("目標と前提値")
    goals = load_goals(cfg)

    st.caption("**目標**（配当は税抜＝手取り基準）")
    g1, g2, g3 = st.columns(3)
    target_age = g1.number_input("目標年齢", value=int(goals["target_age"]),
                                 min_value=30, max_value=90, step=1, key="goal_target_age")
    # 年間と月間を別々に入力できると、片方だけ直したときに達成率（年間ベース）と
    # 想定月収（月間ベース）が食い違う。**年間を唯一の入力**にして月間は12で割る
    dividend_basis = g2.radio(
        "配当目標の入力単位", ["年間", "月間"], horizontal=True, key="goal_dividend_basis",
    )
    if dividend_basis == "年間":
        dividend_annual = float(g3.number_input(
            "年間配当（税抜）", value=int(goals["goal_dividend_annual"]),
            min_value=0, step=100_000, key="goal_annual",
        ))
    else:
        dividend_annual = float(g3.number_input(
            "月間配当（税抜）", value=int(round(goals["goal_dividend_annual"] / 12)),
            min_value=0, step=10_000, key="goal_monthly",
        )) * 12
    dividend_monthly = dividend_annual / 12
    g3.caption(f"年 {yen(dividend_annual)} ／ 月 {yen(dividend_monthly)}")

    n1, _ = st.columns([1, 2])
    net_worth = n1.number_input("総資産", value=int(goals["goal_net_worth"]),
                                min_value=0, step=1_000_000, key="goal_net_worth")

    i1, i2, i3 = st.columns(3)
    withdrawal = i1.number_input("取り崩し月額", value=int(goals["goal_withdrawal_monthly"]),
                                 min_value=0, step=10_000, key="goal_withdrawal")
    business = i2.number_input("事業（月額）", value=int(goals["goal_business_monthly"]),
                               min_value=0, step=10_000, key="goal_business")
    labor = i3.number_input("労働収入（月額）", value=int(goals["goal_labor_monthly"]),
                            min_value=0, step=10_000, key="goal_labor")

    st.caption("**前提値**（シミュレーションの係数。予測や推奨ではない）")
    a1, a2, a3 = st.columns(3)
    purchase_yield = a1.number_input("新規購入の想定利回り（%）",
                                     value=float(goals["assumed_purchase_yield"]),
                                     min_value=0.0, step=0.1, format="%.2f", key="assume_yield")
    index_return = a2.number_input("インデックスの想定年率（%）",
                                   value=float(goals["assumed_index_return"]),
                                   min_value=0.0, step=0.5, format="%.1f", key="assume_return")
    index_monthly = a3.number_input("インデックスへの毎月の積立額",
                                    value=int(goals["assumed_index_monthly"]),
                                    min_value=0, step=10_000, key="assume_monthly")

    s1, s2, s3 = st.columns(3)
    low = s1.number_input("増配シナリオ：保守（%）", value=float(goals["scenario_growth_low"]),
                          min_value=0.0, step=0.5, format="%.1f", key="scenario_low")
    mid = s2.number_input("増配シナリオ：標準（%）", value=float(goals["scenario_growth_mid"]),
                          min_value=0.0, step=0.5, format="%.1f", key="scenario_mid")
    high = s3.number_input("増配シナリオ：強気（%）", value=float(goals["scenario_growth_high"]),
                           min_value=0.0, step=0.5, format="%.1f", key="scenario_high")

    if st.button("目標・前提値を保存", key="save_goals"):
        ok, message = save_goals({
            "target_age": float(target_age),
            "goal_dividend_annual": float(dividend_annual),
            "goal_dividend_monthly": float(dividend_monthly),
            "goal_net_worth": float(net_worth),
            "goal_withdrawal_monthly": float(withdrawal),
            "goal_business_monthly": float(business),
            "goal_labor_monthly": float(labor),
            "assumed_purchase_yield": float(purchase_yield),
            "assumed_index_return": float(index_return),
            "assumed_index_monthly": float(index_monthly),
            "scenario_growth_low": float(low),
            "scenario_growth_mid": float(mid),
            "scenario_growth_high": float(high),
        }, cfg)
        (st.success if ok else st.error)(message)
        # KPI帯はタブより前に描画済み。再実行しないと保存した目標が反映されない
        if ok:
            st.rerun(scope="app")


@st.fragment
def _render_history_editor(history_rows) -> None:
    """受取配当の実績。手入力と汎用CSV取込の両方を置く。"""
    st.subheader("配当実績")
    st.caption(
        "実際に受け取った配当を記録する。証券会社の配当金明細CSVの形式対応は後日。"
        "いまは手入力か、同じ列を持つCSVの取込で入れる。"
    )
    uploaded = st.file_uploader("配当実績CSVを取り込む（任意）", type="csv", key="history_upload")
    rows = list(history_rows)
    if uploaded is not None:
        imported = dh.parse_csv(uploaded.getvalue().decode("utf-8-sig"))
        rows = dh.merge(rows, imported)
        st.info(f"{len(imported)}件を反映中。保存すると確定する（同じ受取は上書き＝二重計上しない）。")

    editable = pd.DataFrame(rows or [], columns=list(dh.HISTORY_COLUMNS))
    for column in ("gross", "tax", "net"):
        editable[column] = pd.to_numeric(editable[column], errors="coerce")
    edited = st.data_editor(
        editable, width="stretch", hide_index=True, num_rows="dynamic", key="history_editor",
        column_config={
            "date": st.column_config.TextColumn("受取日", help="YYYY-MM-DD"),
            "ticker": st.column_config.TextColumn("銘柄コード"),
            "name": st.column_config.TextColumn("名称"),
            "gross": st.column_config.NumberColumn("税引前", min_value=0.0, format="%,d"),
            "tax": st.column_config.NumberColumn("税額", min_value=0.0, format="%,d"),
            "net": st.column_config.NumberColumn("手取り", min_value=0.0, format="%,d",
                                                 help="空欄なら税引前−税額で埋める"),
            "account": st.column_config.SelectboxColumn("口座", options=[""] + list(dataio.ACCOUNTS)),
            "source": st.column_config.TextColumn("証券会社"),
            "note": st.column_config.TextColumn("メモ"),
        },
    )
    if st.button("配当実績を保存", key="save_history"):
        text = dh.serialize_csv(edited.to_dict("records"))
        ok, message = save_side_csv(HISTORY_STATE, HISTORY_PATH, HISTORY_CSV, text, dh.parse_csv,
                                    "update dividend history")
        (st.success if ok else st.error)(message)
        if ok:
            st.rerun(scope="app")


@st.fragment
def _render_snapshot_button(holdings, div_map, cash_rows) -> None:
    """いまの状態を1行記録する。毎月1日の自動記録と同じ処理を手で叩く。"""
    st.subheader("スナップショット")
    st.caption("毎月1日に自動記録される。今すぐ残したいときはここから。同じ月なら上書きされる。")
    if st.button("今の状態を記録", key="record_snapshot"):
        record = sn.build_record(holdings, div_map, cash_total=ca.total(cash_rows))
        current, _ = load_side_csv(SNAPSHOT_STATE, SNAPSHOTS_PATH, SNAPSHOTS_CSV, sn.parse_csv)
        text = sn.serialize_csv(sn.upsert(current, record))
        ok, message = save_side_csv(SNAPSHOT_STATE, SNAPSHOTS_PATH, SNAPSHOTS_CSV, text,
                                    sn.parse_csv, f"record snapshot ({record['date']})")
        (st.success if ok else st.error)(message)
        if ok:
            st.rerun(scope="app")
