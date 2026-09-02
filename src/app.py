"""保有資産 見える化ダッシュボード（Streamlit エントリ）。

実行: streamlit run 400_Asset-management/src/app.py
データソース優先順：アップロードCSV → st.secrets[holdings] → data/holdings.csv → sample
"""
from __future__ import annotations

import os
from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

import dataio
import dividend as dv
import portfolio as pf
import prices as pr
import simulation as sm

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
REAL_CSV = os.path.join(DATA_DIR, "holdings.csv")
SAMPLE_CSV = os.path.join(DATA_DIR, "holdings.sample.csv")
SETTINGS_JSON = os.path.join(DATA_DIR, "user_settings.json")  # 生年月日（.gitignore 済み）

PURPOSE_LABELS = {"dividend": "配当", "yutai": "優待", "": "未分類"}
SOURCE_LABELS = {"rakuten": "楽天", "sbi": "SBI", "": "手入力"}
MARKET_LABELS = {"jp": "日本株", "us": "米国株"}

# バックテスト用の資産クラス代表銘柄（保有銘柄すべての履歴取得は重いため代替する）
# インデックス枠以外は固定。米国上場銘柄を優先するのは、yfinance が日本ETFの
# 株式分割を調整せず履歴が壊れるため（2559 で実害。design-decisions 2026-09-02）。
BACKTEST_BASE_PROXIES = {
    "us_dividend": ("SCHD", "Schwab US Dividend"),
    "jp_dividend": ("1489", "NF日経高配当50"),
    "reit": ("1343", "NF東証REIT"),
}

# インデックス枠（目標配分60%）は投資先の比較ができるよう選択式にする。
# 日本の投資信託（eMAXIS Slim 等）は非上場で yfinance から取得できないため、
# 同じ指数に連動する米国上場ETFで代替する。
BACKTEST_INDEX_CHOICES = {
    "全世界株（VT）": ("VT", "Vanguard Total World Stock"),
    "S&P500（VOO）": ("VOO", "Vanguard S&P 500"),
}


@st.cache_data(ttl=3600, show_spinner="時価を取得中…")
def cached_prices(tickers: tuple[str, ...]) -> dict[str, float]:
    """yfinance 取得のキャッシュ層。prices.py は純粋なまま保つ（設計方針）。

    Streamlit はウィジェット操作のたびに全スクリプトを再実行するため、
    キャッシュがないと軸切替・税込税抜切替の度に全銘柄の逐次API取得が走り実用に耐えない。
    """
    return pr.fetch_prices(list(tickers))


@st.cache_data(ttl=3600, show_spinner="配当を取得中…")
def cached_dividends(tickers: tuple[str, ...]) -> dict[str, float]:
    return pr.fetch_dividends(list(tickers))


@st.cache_data(ttl=3600, show_spinner="権利確定月を取得中…")
def cached_dividend_months(tickers: tuple[str, ...]) -> dict[str, list[int]]:
    return pr.fetch_dividend_months(list(tickers))


@st.cache_data(ttl=3600, show_spinner="為替レートを取得中…")
def cached_fx_rate() -> float | None:
    return pr.fetch_fx_rate()


@st.cache_data(persist="disk", show_spinner="価格履歴を取得中…")
def cached_price_history(tickers: tuple[str, ...], years: int):
    """バックテスト用の価格履歴。取得が重いためディスクにも残す（再起動後も再利用）。"""
    return pr.fetch_price_history(list(tickers), years)


def load_birth_date() -> date | None:
    """保存済みの生年月日を読む。未保存・読めない場合は None（＝未設定）。"""
    try:
        with open(SETTINGS_JSON, encoding="utf-8") as f:
            return dataio.parse_birth_date(f.read())
    except OSError:
        return None


def save_birth_date(birth: date) -> bool:
    """生年月日をローカルへ保存する。書き込めない環境（クラウド等）では False。"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SETTINGS_JSON, "w", encoding="utf-8") as f:
            f.write(dataio.serialize_birth_date(birth))
        return True
    except OSError:
        return False


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


def _render_simple_allocation(
    axis: str, alloc: dict[str, float], label_map: dict[str, str], left, right
) -> None:
    """目標値が未定義の軸（商品種別・上場市場）の構成比を円グラフ＋表で描く。

    資産クラス軸と違い目標AAがないため「ズレ」列は出さない。
    """
    if not alloc:
        left.caption("該当なし")
        return

    labels = [label_map.get(k, k) for k in alloc]
    pie_df = pd.DataFrame({axis: labels, "構成比": list(alloc.values())})
    fig = px.pie(pie_df, names=axis, values="構成比", title="現在の構成比")
    left.plotly_chart(fig, width="stretch")

    table_df = pd.DataFrame(
        {axis: labels, "現在%": [round(v, 1) for v in alloc.values()]}
    ).sort_values("現在%", ascending=False)
    right.dataframe(table_df, width="stretch", hide_index=True)


def _render_birth_date_input() -> date | None:
    """サイドバーで生年月日を入力・保存し、現在の設定値を返す。

    保存はローカルファイル。Streamlit Cloud のようにコンテナが揮発する環境では
    再起動で消えるため、その場合は都度入力になる（保存失敗を画面で伝える）。
    """
    st.sidebar.subheader("シミュレーション設定")
    saved = load_birth_date()
    birth = st.sidebar.date_input(
        "生年月日",
        value=saved or date(1980, 1, 1),
        min_value=date(1930, 1, 1),
        max_value=date.today(),
        format="YYYY/MM/DD",
        help="年齢を自動計算してシミュレーションの期間に使う。ローカルにのみ保存する。",
    )

    if saved is None:
        st.sidebar.caption("未保存。保存すると次回から自動で読み込む。")
    elif birth != saved:
        st.sidebar.caption(f"保存済み：{saved}（変更後は保存を押す）")

    if st.sidebar.button("生年月日を保存"):
        if save_birth_date(birth):
            st.sidebar.success(f"保存した（{birth}）")
        else:
            st.sidebar.warning("保存できなかった（書き込み不可の環境）。今回のみ有効。")
    return birth


def _render_accumulation_tab(current_value: float, years: int) -> None:
    """つみたてタブ：将来の資産推移（投下元本と評価額）。"""
    c1, c2, c3 = st.columns(3)
    initial = c1.number_input(
        "現在の資産額", value=float(round(current_value)), step=100_000.0, format="%.0f"
    )
    monthly = c2.number_input(
        "毎月の積立額", value=sm.DEFAULT_MONTHLY_CONTRIBUTION, step=10_000.0, format="%.0f"
    )
    annual_return = c3.number_input(
        "想定年率リターン（%）", value=sm.DEFAULT_ANNUAL_RETURN, step=0.5, format="%.1f"
    )

    points = sm.project_accumulation(initial, monthly, years, annual_return)
    last = points[-1]

    m1, m2, m3 = st.columns(3)
    m1.metric(f"{years}年後の評価額", yen(last.value))
    m2.metric("投下元本", yen(last.principal))
    m3.metric("運用益", yen(last.value - last.principal))

    df = pd.DataFrame(
        {
            "経過年": [p.year for p in points] * 2,
            "金額": [p.principal for p in points] + [p.value for p in points],
            "区分": ["投下元本"] * len(points) + ["評価額"] * len(points),
        }
    )
    fig = px.line(df, x="経過年", y="金額", color="区分", title="資産推移（想定）")
    st.plotly_chart(fig, width="stretch")


def _render_dividend_cf_tab(
    current_annual_dividend: float, current_yield: float, years: int, target_age: int
) -> None:
    """配当CFタブ：目標（月6〜10万）への到達見込み。"""
    c1, c2, c3 = st.columns(3)
    monthly = c1.number_input(
        "毎月の積立額", value=sm.DEFAULT_MONTHLY_CONTRIBUTION, step=10_000.0,
        format="%.0f", key="cf_monthly",
    )
    dividend_yield = c2.number_input(
        "想定配当利回り（%）",
        value=float(round(current_yield, 2)) if current_yield > 0 else 4.0,
        step=0.1, format="%.2f",
    )
    dividend_growth = c3.number_input(
        "想定増配率（%/年）", value=sm.DEFAULT_DIVIDEND_GROWTH, step=0.5, format="%.1f"
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

    points = sm.project_dividend_cf(
        current_annual_dividend=current_annual_dividend,
        monthly=monthly,
        years=years,
        dividend_yield=dividend_yield,
        dividend_growth=dividend_growth,
        income_ratio=income_ratio / 100.0,
    )
    last = points[-1]
    last_monthly = last.monthly_pre_tax if pre_tax else last.monthly_after_tax
    reach_year = sm.first_year_reaching(points, sm.TARGET_CF_MONTHLY_MIN, pre_tax=pre_tax)

    m1, m2, m3 = st.columns(3)
    m1.metric(f"{target_age}歳時点の月額配当（{tax_mode}）", yen(last_monthly))
    m2.metric("年間配当", yen(last.annual_pre_tax if pre_tax else last.annual_after_tax))
    if reach_year is None:
        m3.metric("月6万到達", "期間内に未到達")
    else:
        m3.metric("月6万到達", f"{reach_year}年後")

    values = [p.monthly_pre_tax if pre_tax else p.monthly_after_tax for p in points]
    df = pd.DataFrame({"経過年": [p.year for p in points], "月額配当": values})
    fig = px.line(df, x="経過年", y="月額配当", title=f"月額配当CFの推移（{tax_mode}）")
    fig.add_hrect(
        y0=sm.TARGET_CF_MONTHLY_MIN, y1=sm.TARGET_CF_MONTHLY_MAX,
        fillcolor="green", opacity=0.12, line_width=0,
        annotation_text="目標帯 月6〜10万", annotation_position="top left",
    )
    st.plotly_chart(fig, width="stretch")


def _render_backtest_tab() -> None:
    """バックテストタブ：目標AAで過去の積立を再現し、インデックス投資先を比較する。"""
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
    us_tickers = {str(r["ticker"]).strip() for r in rows if str(r.get("market", "")).strip() == "us"}
    use_live = st.sidebar.checkbox("時価を yfinance から取得", value=True)

    birth_date = _render_birth_date_input()

    fx_rate = cached_fx_rate() if use_live else None
    price_map = cached_prices(tuple(tickers)) if use_live else {}
    if use_live:
        price_map = pr.convert_us_values_to_jpy(price_map, us_tickers, fx_rate)
    if use_live and not price_map:
        # 全銘柄で取れないのは通信不可のほかに API 仕様変更やIP制限もありうる
        # （2026-09-02：yfinance のキーが camelCase 化して全滅／クラウドは Yahoo が 401）
        saved = {str(r.get("price_asof", "")).strip() for r in rows if str(r.get("price", "")).strip()}
        saved.discard("")
        if saved:
            st.info(
                f"ライブ時価を取得できないため、取込時に保存した時価で評価します（{max(saved)} 時点）。"
            )
        else:
            st.warning(
                "時価を取得できず、保存された時価もありません（取得単価で評価するため含み損益は0）。"
                "ローカルで `python scripts/import_holdings.py --refresh-prices` を実行すると保存されます。"
            )

    holdings = pf.build_holdings(rows, price_map)

    # 配当データ：CSV div_per_share を優先、空は yfinance で補完
    div_map = {str(r["ticker"]).strip(): float(r["div_per_share"])
               for r in rows if str(r.get("div_per_share", "")).strip() not in ("", "nan")}
    months_map: dict[str, list[int]] = {}
    if use_live:
        missing = [t for t in tickers if t not in div_map]
        if missing:
            fetched_div = pr.convert_us_values_to_jpy(
                cached_dividends(tuple(missing)), us_tickers, fx_rate
            )
            div_map.update(fetched_div)
        months_map = cached_dividend_months(tuple(tickers))

    # --- サマリー ---
    c1, c2, c3 = st.columns(3)
    cost = pf.total_cost(holdings)
    market = pf.total_market(holdings)
    gain = pf.total_gain(holdings)
    c1.metric("総取得額", yen(cost))
    c2.metric("総評価額", yen(market))
    c3.metric("含み損益", yen(gain), f"{pf.total_gain_rate(holdings):+.2f}%")

    # --- AA 円グラフ vs 目標（軸切替：資産クラス／商品種別／上場市場） ---
    # 軸名はデータの実態に合わせている。sector 列は業種でなく商品種別、
    # market 列は上場市場（投資対象地域ではない）。
    st.subheader("アセットアロケーション")
    axis = st.radio(
        "集計軸", ["資産クラス", "商品種別", "上場市場"], horizontal=True, key="alloc_axis"
    )
    left, right = st.columns([1, 1])

    if axis == "資産クラス":
        alloc = pf.allocation_by_class(holdings)
        drift = pf.allocation_drift(holdings)
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
    elif axis == "商品種別":
        _render_simple_allocation(axis, pf.allocation_by_sector(holdings), {}, left, right)
        st.caption("holdings.csv の sector 列。業種（電気機器・銀行 等）ではなく商品種別。")
    else:
        _render_simple_allocation(
            axis, pf.allocation_by_market_region(holdings), MARKET_LABELS, left, right
        )
        st.caption(
            "上場市場ベース。東証上場のオルカン・S&P500 ETF/投信は「日本株」に計上される"
            "（投資対象地域ではない）。"
        )

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
    mkt_df = pd.DataFrame(
        {"市場": [MARKET_LABELS.get(k, k) for k in by_mkt], "配当": [round(v) for v in by_mkt.values()]}
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
                "市場": MARKET_LABELS.get(h.market, h.market),
                "用途": PURPOSE_LABELS.get(h.purpose, "未分類"),
                "証券会社": SOURCE_LABELS.get(h.source, h.source or "手入力"),
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

    # --- 日本個別株：高配当・優待 ---
    st.subheader("日本個別株：高配当・優待")
    by_purpose = pf.jp_dividend_by_purpose(holdings)
    tab_dividend, tab_yutai, tab_unclassified = st.tabs(["高配当", "優待", "未分類"])
    for tab, key in ((tab_dividend, "dividend"), (tab_yutai, "yutai"), (tab_unclassified, "")):
        group = by_purpose.get(key, [])
        with tab:
            if not group:
                st.caption("該当なし")
                continue
            purpose_df = pd.DataFrame(
                [
                    {
                        "銘柄": h.ticker,
                        "名称": h.name,
                        "株数": h.shares,
                        "取得単価": h.cost_per_share,
                        "評価額": round(h.market_value),
                        "含み損益": round(h.gain),
                    }
                    for h in group
                ]
            )
            st.dataframe(purpose_df, width="stretch", hide_index=True)

    # --- シミュレーション ---
    st.subheader("シミュレーション")
    target_age = st.number_input(
        "目標年齢", value=sm.DEFAULT_TARGET_AGE, min_value=1, max_value=120, step=1
    )
    current_age = sm.age_at(birth_date, date.today())
    years = sm.years_until_age(birth_date, date.today(), int(target_age))
    st.caption(
        f"現在 {current_age}歳 → {int(target_age)}歳まで残り {years}年。"
        "以下の数値は入力した前提で計算した結果であり、将来の予測や推奨ではない。"
    )
    if years == 0:
        st.info("目標年齢に到達済み。目標年齢を引き上げると将来推移を確認できる。")

    tab_acc, tab_cf, tab_bt = st.tabs(["つみたて", "配当CF", "バックテスト"])
    with tab_acc:
        _render_accumulation_tab(market, years)
    with tab_cf:
        _render_dividend_cf_tab(
            current_annual_dividend=dv.total_annual_dividend(holdings, div_map, pre_tax=True),
            current_yield=dv.yield_on_market(holdings, div_map),
            years=years,
            target_age=int(target_age),
        )
    with tab_bt:
        _render_backtest_tab()


if __name__ == "__main__":
    main()
