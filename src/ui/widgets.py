"""どのタブからも使う描画部品（表・円グラフ・折れ線・KPIセル）。"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

import portfolio as pf
import snapshots as sn
import storage as sg
import viewsettings as vs

from ui.appdata import save_view_orders
from ui.constants import VIEW_ORDERS_STATE


def show_table(
    df: pd.DataFrame,
    container=None,
    decimals: dict[str, int] | None = None,
    order_key: str | None = None,
    cfg: sg.StorageConfig | None = None,
) -> None:
    """数値列に桁区切りを付けて表を描く。

    文字列に変換せず pandas の Styler を使うのは、**数値としてのソートを保つ**ため
    （列ヘッダをクリックしたときに文字列順に並ぶと表として使えない）。

    小数桁は列ごとに自動判定する（全て整数なら0桁、小数を含むなら2桁）。
    株数・評価額のような整数量に不要な小数点を出さないためで、明示したい列は
    decimals で上書きする。

    order_key を渡すと列の並び替え UI を出す。Streamlit の表は**列のドラッグ移動に
    対応していない**ため、multiselect の選択順（選んだ順に返る）を column_order に渡して
    実現する。「この並びを保存」を押すと保存先に残り、次回以降その並びで開く。
    """
    target = container if container is not None else st
    columns = list(df.columns)

    if order_key:
        if order_key not in st.session_state:
            # 初回だけ保存済みの並びを入れる。key 付き multiselect は session_state が
            # default より優先されるため、default では保存値を反映できない
            saved = st.session_state.get(VIEW_ORDERS_STATE, {}).get(order_key)
            st.session_state[order_key] = vs.resolve_order(saved, columns)

        with target.expander("列の並び替え・表示", expanded=False):
            chosen = st.multiselect(
                "選んだ順に左から並びます（外した列は非表示）", columns, key=order_key,
            )
            # 全部外すと空の表になってしまうので、その場合は元の並びに戻す
            columns = chosen or list(df.columns)

            if cfg is not None and st.button("この並びを保存", key=f"save_{order_key}"):
                orders = dict(st.session_state.get(VIEW_ORDERS_STATE, {}))
                orders[order_key] = columns
                ok, message = save_view_orders(cfg, orders)
                if ok:
                    st.session_state[VIEW_ORDERS_STATE] = orders
                    st.success("この並びを次回以降も使います。")
                else:
                    st.error(message)

    formats: dict[str, str] = {}
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            continue
        digits = (decimals or {}).get(col)
        if digits is None:
            values = df[col].dropna()
            digits = 0 if len(values) == 0 or bool((values % 1 == 0).all()) else 2
        formats[col] = f"{{:,.{digits}f}}"

    target.dataframe(
        df.style.format(formats), width="stretch", hide_index=True, column_order=columns
    )


def pie(values: dict[str, float], name_label: str, title: str, group_small: bool = True):
    """凡例ではなくスライスの外側に名前を出す円グラフ。

    既定の凡例方式は、色と名前を目で突き合わせないとどのスライスか分からない。
    小さいスライスは `group_small_slices` で1つにまとめる（ラベルの重なり防止）。
    明細は必ず隣の表に出しているので、図は上位の把握に振り切る。
    """
    positive = {k: v for k, v in values.items() if v > 0}
    shown = pf.group_small_slices(positive) if group_small else positive
    df = pd.DataFrame({name_label: list(shown), "値": list(shown.values())})
    fig = px.pie(df, names=name_label, values="値", title=title)
    fig.update_traces(
        textposition="outside",
        # 既定は有効桁数で揃うため 8.06% と 16.8% が混在する。小数1桁に統一して読みやすくする
        texttemplate="%{label}<br>%{percent:.1%}",
        # 外側ラベルは自動で折り返されないため、はみ出しは automargin で吸収する
        automargin=True,
        hovertemplate="%{label}<br>%{percent}<extra></extra>",
    )
    fig.update_layout(showlegend=False, uniformtext_minsize=10, height=420)
    return fig


def simple_allocation(
    axis: str, alloc: dict[str, float], label_map: dict[str, str], left, right,
    cfg: sg.StorageConfig | None = None,
) -> None:
    """目標値が未定義の軸（商品種別・上場市場）の構成比を円グラフ＋表で描く。

    資産クラス軸と違い目標AAがないため「ズレ」列は出さない。
    """
    if not alloc:
        left.caption("該当なし")
        return

    labels = [label_map.get(k, k) for k in alloc]
    left.plotly_chart(
        pie(dict(zip(labels, alloc.values())), axis, "現在の構成比"),
        width="stretch",
    )

    table_df = pd.DataFrame(
        {axis: labels, "現在%": [round(v, 1) for v in alloc.values()]}
    ).sort_values("現在%", ascending=False)
    show_table(table_df, right, order_key=f"cols_alloc_{axis}", cfg=cfg)


def snapshot_notice(snapshot_rows) -> bool:
    """記録が2件未満なら案内を出す。推移を描けるかどうかを返す。"""
    if len(snapshot_rows) >= 2:
        return True
    st.info(
        f"推移はスナップショットが2回分たまってから表示する（現在 {len(snapshot_rows)} 件）。"
        "毎月1日に自動記録され、データタブの「今の状態を記録」でも増やせる。"
    )
    return False


def line_chart(snapshot_rows, columns: dict[str, str], title: str) -> None:
    """スナップショットの複数列を1枚の折れ線にする。columns={列名: 表示名}。"""
    labels, _ = sn.series(snapshot_rows, "date")
    frame = {"日付": labels}
    for column, name in columns.items():
        frame[name] = sn.series(snapshot_rows, column)[1]
    df = pd.DataFrame(frame)
    fig = px.line(df, x="日付", y=list(columns.values()), title=title, markers=True)
    fig.update_layout(legend_title_text="", yaxis_title="円")
    st.plotly_chart(fig, width="stretch")


def kpi_cell(column, label: str, value: str, side: str = "", tone: str = "",
             subs: tuple[str, ...] = (), progress: float | None = None,
             divider: bool = False) -> None:
    """KPI 1項目。主数字は円のフル桁、右横に補足（前月比・現在/目標）、下に薄い補足行。

    divider=True で左に区切り線を引く（2列目以降に付けて項目の境界を示す）。
    """
    side_html = f'<span class="kpi-side {tone}">{side}</span>' if side else ""
    bar_html = ""
    if progress is not None:
        width = min(max(progress, 0.0), 1.0) * 100.0
        bar_html = f'<div class="kpi-track"><span style="width:{width:.1f}%"></span></div>'
    subs_html = "".join(f'<div class="kpi-sub">{text}</div>' for text in subs)
    column.markdown(
        f'<div class="kpi{" kpi-divider" if divider else ""}">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-main"><span class="kpi-value">{value}</span>{side_html}</div>'
        f'{bar_html}{subs_html}</div>',
        unsafe_allow_html=True,
    )
