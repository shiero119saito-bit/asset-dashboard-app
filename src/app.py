"""保有資産 見える化ダッシュボード（Streamlit エントリ）。

実行: streamlit run 400_Asset-management/src/app.py
データソース優先順：アップロードCSV → st.secrets[holdings] → data/holdings.csv → sample
"""
from __future__ import annotations

import os

import pandas as pd
import plotly.express as px
import streamlit as st

import dataio
import dividend as dv
import portfolio as pf
import prices as pr

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
REAL_CSV = os.path.join(DATA_DIR, "holdings.csv")
SAMPLE_CSV = os.path.join(DATA_DIR, "holdings.sample.csv")


def _secrets_holdings_csv() -> str | None:
    """st.secrets から保有CSV文字列を防御的に取得。未設定なら None（ローカルで安全）。"""
    try:
        return st.secrets["holdings"]["csv"]
    except Exception:
        return None


def load_rows(uploaded_text: str | None = None) -> tuple[list[dict], str]:
    """保有データを多段ソースから読む。(rows, 使用ソースラベル) を返す。

    優先順：①アップロードCSV ②st.secrets[holdings][csv]（クラウド永続）
            ③data/holdings.csv（ローカル実データ）④sample
    """
    if uploaded_text is not None and uploaded_text.strip() != "":
        return dataio.parse_holdings_csv(uploaded_text), "アップロードCSV"

    secret_csv = _secrets_holdings_csv()
    if secret_csv:
        return dataio.parse_holdings_csv(secret_csv), "secrets（クラウド）"

    if os.path.exists(REAL_CSV):
        df = pd.read_csv(REAL_CSV)
        return df.to_dict("records"), "holdings.csv"

    df = pd.read_csv(SAMPLE_CSV)
    return df.to_dict("records"), "holdings.sample.csv（サンプル）"


def yen(v: float) -> str:
    return f"¥{v:,.0f}"


def main() -> None:
    st.set_page_config(page_title="資産ダッシュボード", layout="wide")
    st.title("保有資産 見える化ダッシュボード")

    st.sidebar.subheader("データ")
    uploaded = st.sidebar.file_uploader("保有CSVをアップロード（任意）", type="csv")
    uploaded_text = uploaded.getvalue().decode("utf-8-sig") if uploaded is not None else None

    try:
        rows, src = load_rows(uploaded_text)
    except ValueError as e:
        st.error(f"CSVの読み込みに失敗しました：{e}")
        st.stop()

    st.sidebar.caption(f"データソース：{src}")
    if "サンプル" in src:
        st.warning("サンプルデータを表示中です。実データはCSVアップロード、または data/holdings.csv で表示されます。")

    tickers = [str(r["ticker"]).strip() for r in rows]
    use_live = st.sidebar.checkbox("時価を yfinance から取得", value=True)
    price_map = pr.fetch_prices(tickers) if use_live else {}
    if use_live and not price_map:
        st.info("時価を取得できませんでした（オフライン/未導入）。取得単価で評価します。")

    holdings = pf.build_holdings(rows, price_map)

    # 配当データ：CSV div_per_share を優先、空は yfinance で補完
    div_map = {str(r["ticker"]).strip(): float(r["div_per_share"])
               for r in rows if str(r.get("div_per_share", "")).strip() not in ("", "nan")}
    months_map: dict[str, list[int]] = {}
    if use_live:
        missing = [t for t in tickers if t not in div_map]
        if missing:
            div_map.update(pr.fetch_dividends(missing))
        months_map = pr.fetch_dividend_months(tickers)

    # --- サマリー ---
    c1, c2, c3 = st.columns(3)
    cost = pf.total_cost(holdings)
    market = pf.total_market(holdings)
    gain = pf.total_gain(holdings)
    c1.metric("総取得額", yen(cost))
    c2.metric("総評価額", yen(market))
    c3.metric("含み損益", yen(gain), f"{pf.total_gain_rate(holdings):+.2f}%")

    # --- AA 円グラフ vs 目標 ---
    st.subheader("アセットアロケーション")
    alloc = pf.allocation_by_class(holdings)
    drift = pf.allocation_drift(holdings)
    left, right = st.columns([1, 1])

    pie_df = pd.DataFrame(
        {
            "資産クラス": [pf.ASSET_CLASS_LABELS[ac] for ac in pf.ASSET_CLASSES],
            "構成比": [alloc[ac] for ac in pf.ASSET_CLASSES],
        }
    )
    fig = px.pie(pie_df, names="資産クラス", values="構成比", title="現在の構成比")
    left.plotly_chart(fig, width="stretch")

    drift_df = pd.DataFrame(
        {
            "資産クラス": [pf.ASSET_CLASS_LABELS[ac] for ac in pf.ASSET_CLASSES],
            "現在%": [round(alloc[ac], 1) for ac in pf.ASSET_CLASSES],
            "目標%": [pf.TARGET_ALLOCATION[ac] for ac in pf.ASSET_CLASSES],
            "ズレ": [round(drift[ac], 1) for ac in pf.ASSET_CLASSES],
        }
    )
    right.dataframe(drift_df, width="stretch", hide_index=True)

    # --- 配当 ---
    st.subheader("配当")
    tax_mode = st.radio("表示", ["税込", "税抜"], horizontal=True, key="tax_mode")
    pre_tax = tax_mode == "税込"

    d1, d2, d3 = st.columns(3)
    annual_div = dv.total_annual_dividend(holdings, div_map, pre_tax=pre_tax)
    d1.metric(f"年間配当（{tax_mode}）", yen(annual_div))
    d2.metric("取得額利回り", f"{dv.yield_on_cost(holdings, div_map):.2f}%")
    d3.metric("評価額利回り", f"{dv.yield_on_market(holdings, div_map):.2f}%")
    if not div_map:
        st.info("配当データがありません。holdings.csv の div_per_share を入力するか、時価取得をONにしてください。")

    # 権利確定月別
    by_month = dv.dividend_by_month(holdings, div_map, months_map, pre_tax=pre_tax)
    month_labels = [f"{m}月" for m in range(1, 13)] + [dv.UNKNOWN_MONTH]
    month_values = [by_month[m] for m in range(1, 13)] + [by_month[dv.UNKNOWN_MONTH]]
    month_df = pd.DataFrame({"月": month_labels, "配当": [round(v) for v in month_values]})
    fig_month = px.bar(month_df, x="月", y="配当", title=f"権利確定月別 配当（{tax_mode}）")
    st.plotly_chart(fig_month, width="stretch")

    # セクター別 / 日米別
    s_col, m_col = st.columns(2)
    by_sector = dv.dividend_by_sector(holdings, div_map, pre_tax=pre_tax)
    sector_df = pd.DataFrame(
        {"セクター": list(by_sector.keys()), "配当": [round(v) for v in by_sector.values()]}
    ).sort_values("配当", ascending=False)
    s_col.dataframe(sector_df, width="stretch", hide_index=True)

    by_mkt = dv.dividend_by_market(holdings, div_map, pre_tax=pre_tax)
    mkt_label = {"jp": "日本株", "us": "米国株"}
    mkt_df = pd.DataFrame(
        {"市場": [mkt_label.get(k, k) for k in by_mkt], "配当": [round(v) for v in by_mkt.values()]}
    )
    fig_mkt = px.pie(mkt_df, names="市場", values="配当", title="日米別 配当")
    m_col.plotly_chart(fig_mkt, width="stretch")

    # --- 銘柄テーブル ---
    st.subheader("銘柄別")
    table = pd.DataFrame(
        [
            {
                "銘柄": h.ticker,
                "名称": h.name,
                "クラス": pf.ASSET_CLASS_LABELS[h.asset_class],
                "セクター": h.sector,
                "市場": mkt_label.get(h.market, h.market),
                "株数": h.shares,
                "取得単価": h.cost_per_share,
                "現在値": round(h.price, 2),
                "評価額": round(h.market_value),
                "含み損益": round(h.gain),
                "損益率%": round(h.gain_rate, 2),
                "構成比%": round(h.market_value / market * 100, 1) if market else 0.0,
                f"年間配当({tax_mode})": round(dv.holding_dividend(h, div_map, pre_tax)),
            }
            for h in holdings
        ]
    )
    st.dataframe(table, width="stretch", hide_index=True)


if __name__ == "__main__":
    main()
