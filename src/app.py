"""保有資産 見える化ダッシュボード（Streamlit エントリ）。

実行: streamlit run 400_Asset-management/src/app.py
データソース優先順：アップロードCSV → st.secrets[holdings] → data/holdings.csv → sample
"""
from __future__ import annotations

import dataclasses
import os
from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

import dataio
import dividend as dv
import portfolio as pf
import prices as pr
import pricing_update as pu
import simulation as sm
import storage as sg
import viewsettings as vs

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
REAL_CSV = os.path.join(DATA_DIR, "holdings.csv")
SAMPLE_CSV = os.path.join(DATA_DIR, "holdings.sample.csv")
SETTINGS_JSON = os.path.join(DATA_DIR, "user_settings.json")  # 生年月日（.gitignore 済み）

# 保有目的。当初は日本個別株の「高配当か優待か」を分ける列だったが、
# インデックス（オルカン等）は資産最大化が目的で配当も優待も目的ではないため growth を足した。
# 空文字は未分類（取込直後の既定値）。
PURPOSE_LABELS_BY_VALUE = {"dividend": "配当", "growth": "資産形成", "yutai": "優待"}
PURPOSE_LABELS = {**PURPOSE_LABELS_BY_VALUE, "": "未分類"}
SOURCE_LABELS = {"rakuten": "楽天", "sbi": "SBI", "": "手入力"}
MARKET_LABELS = {"jp": "日本株", "us": "米国株"}

# 口座区分。特定以外は配当の国内課税が非課税になる（米国株の源泉10%は残る）。
# 空欄は特定扱い＝課税（未設定のデータを非課税と誤表示しないための安全側）
ACCOUNT_LABELS = {
    "specific": "特定",
    "nisa_old": "旧NISA",
    "nisa_tsumitate": "つみたて投資枠",
    "nisa_growth": "成長投資枠",
    "": "特定",
}

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


def load_birth_date(cfg: sg.StorageConfig | None = None) -> date | None:
    """保存済みの生年月日を読む。未保存・読めない場合は None（＝未設定）。

    保存先（private repo）→ ローカルファイルの順に探す。Streamlit Cloud は
    コンテナが揮発するためローカルに書いても再起動で消える。保存先があればそちらを正とする。
    """
    text, _ = sg.load(user_settings_config(cfg))
    if text:
        return dataio.parse_birth_date(text)
    try:
        with open(SETTINGS_JSON, encoding="utf-8") as f:
            return dataio.parse_birth_date(f.read())
    except OSError:
        return None


def save_birth_date(birth: date, cfg: sg.StorageConfig | None = None) -> tuple[bool, str]:
    """生年月日を保存する。(成功したか, 利用者向けメッセージ)。

    保存先があれば private repo へ書く（端末をまたいで残る）。無ければ従来どおり
    ローカルファイルへ書く（クラウドでは再起動で消えるため、その旨を返す）。
    """
    if cfg is not None:
        target = user_settings_config(cfg)
        _, sha = sg.load(target)
        ok, message = sg.save(target, dataio.serialize_birth_date(birth), sha, "update birth date")
        return (ok, f"保存した（{birth}）" if ok else message)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SETTINGS_JSON, "w", encoding="utf-8") as f:
            f.write(dataio.serialize_birth_date(birth))
        return (True, f"保存した（{birth}）")
    except OSError:
        return (False, "保存できなかった（書き込み不可の環境）。今回のみ有効。")


def _secrets_holdings_csv() -> str | None:
    """st.secrets から保有CSV文字列を防御的に取得。未設定なら None（ローカルで安全）。"""
    try:
        return st.secrets["holdings"]["csv"]
    except Exception:
        return None


def storage_config() -> sg.StorageConfig | None:
    """st.secrets[storage] から保存先設定を防御的に取得。未設定なら None。"""
    try:
        raw = dict(st.secrets["storage"])
    except Exception:
        return None
    return sg.build_config(raw)


def load_rows(uploaded_text: str | None = None) -> tuple[list[dict], str, str | None]:
    """保有データを多段ソースから読む。(rows, ソースラベル, storage の sha) を返す。

    優先順：①アップロードCSV ②private repo（画面編集の保存先）
            ③st.secrets[holdings][csv] ④data/holdings.csv ⑤sample

    storage を secrets より優先するのは、画面で編集した最新が常に勝つようにするため。
    sha は保存時の競合検知に使う（storage 以外から読んだ場合は None）。
    """
    if uploaded_text is not None and uploaded_text.strip() != "":
        return dataio.parse_holdings_csv(uploaded_text), "アップロードCSV", None

    stored_text, sha = sg.load(storage_config())
    if stored_text:
        return dataio.parse_holdings_csv(stored_text), "保存先（自動同期）", sha

    secret_csv = _secrets_holdings_csv()
    if secret_csv:
        return dataio.parse_holdings_csv(secret_csv), "secrets（クラウド）", None

    if os.path.exists(REAL_CSV):
        df = pd.read_csv(REAL_CSV)
        return df.to_dict("records"), "holdings.csv", None

    df = pd.read_csv(SAMPLE_CSV)
    return df.to_dict("records"), "holdings.sample.csv（サンプル）", None


def yen(v: float) -> str:
    return f"¥{v:,.0f}"


# 設定類は保有データと同じ repo の別ファイルに置く。session_state もローカルファイルも
# ブラウザを閉じる／コンテナが再起動すると消えるため、端末をまたいで残らない
VIEW_SETTINGS_PATH = "view_settings.json"   # 列の並び順
USER_SETTINGS_PATH = "user_settings.json"   # 生年月日（機微情報。repo は Private）
VIEW_ORDERS_STATE = "view_orders"
BIRTH_DATE_STATE = "birth_date"


def view_settings_config(cfg: sg.StorageConfig | None) -> sg.StorageConfig | None:
    """保存先設定の path だけを表示設定ファイルに差し替える。"""
    return dataclasses.replace(cfg, path=VIEW_SETTINGS_PATH) if cfg else None


def user_settings_config(cfg: sg.StorageConfig | None) -> sg.StorageConfig | None:
    """保存先設定の path だけを個人設定ファイルに差し替える。"""
    return dataclasses.replace(cfg, path=USER_SETTINGS_PATH) if cfg else None


def load_view_orders(cfg: sg.StorageConfig | None) -> dict[str, list[str]]:
    """保存された列順を読む。未設定・未作成なら空 dict（＝既定の並び）。"""
    text, _ = sg.load(view_settings_config(cfg))
    return vs.parse_orders(text)


def save_view_orders(cfg: sg.StorageConfig | None, orders: dict[str, list[str]]) -> tuple[bool, str]:
    """列順を保存先へ書き戻す。sha を取り直してから書く（他端末の更新を弾く）。"""
    target = view_settings_config(cfg)
    _, sha = sg.load(target)
    return sg.save(target, vs.serialize_orders(orders), sha, "update view settings")


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


def _summarize(group: list[pf.Holding], label_of) -> str:
    """グループ内の値の内訳を1セルに収める（例「特定・成長投資枠」）。重複は畳む。"""
    return "・".join(dict.fromkeys(label_of(h) for h in group))


def _account_summary(group: list[pf.Holding]) -> str:
    """口座区分の内訳。空欄は特定として表示する（税計算の扱いと揃える）。"""
    return _summarize(group, lambda h: ACCOUNT_LABELS.get(h.account, h.account or "特定"))


def _merged_row(
    group: list[pf.Holding],
    total_market: float,
    price_map: dict[str, float],
    div_map: dict[str, float],
    pre_tax: bool,
    tax_mode: str,
) -> dict:
    """同一銘柄の保有（口座別に分かれている）を1行にまとめる。

    **配当だけは合算前に口座別へ税率を当てる**必要がある（NISA は非課税）。
    株数や評価額は単純合計でよいが、取得単価は加重平均にする。
    """
    head = group[0]
    shares = sum(h.shares for h in group)
    cost = sum(h.cost_value for h in group)
    value = sum(h.market_value for h in group)
    gain = value - cost
    return {
        "銘柄": head.ticker,
        "名称": head.name,
        "クラス": pf.ASSET_CLASS_LABELS[head.asset_class],
        "セクター": head.sector,
        "市場": MARKET_LABELS.get(head.market, head.market),
        "用途": PURPOSE_LABELS.get(head.purpose, "未分類"),
        "口座": _account_summary(group),
        # 同じ銘柄を複数の証券会社で持つことがあるので、先頭だけでなく内訳を出す
        "証券会社": _summarize(group, lambda h: SOURCE_LABELS.get(h.source, h.source or "手入力")),
        "株数": shares,
        "取得単価": pf.merged_cost_per_share(group),
        "現在値": round(head.price, 2),
        "時価": head.price_asof or ("ライブ" if price_map.get(head.ticker) else "取得単価"),
        "評価額": round(value),
        "含み損益": round(gain),
        "損益率%": round(gain / cost * 100, 2) if cost else 0.0,
        "構成比%": round(value / total_market * 100, 1) if total_market else 0.0,
        f"年間配当({tax_mode})": round(
            sum(dv.holding_dividend(h, div_map, pre_tax) for h in group)
        ),
    }


def _render_price_refresh(cfg: sg.StorageConfig | None) -> None:
    """時価の更新導線。保存先があれば GitHub Actions に依頼するボタンを出す。

    アプリ自身は Yahoo から時価を取れない（Streamlit Cloud の IP が 401 で弾かれる）ため、
    取得できる場所＝Actions に肩代わりさせる。押した時だけ起動する（自動起動にしない）：
    Streamlit は操作のたびにスクリプト全体を再実行するため、自動にすると画面を触るたびに
    Actions が走ってしまう。
    """
    if cfg is None:
        st.caption(
            "PCで `python 400_Asset-management/scripts/refresh_prices.py --local` を実行すると"
            " price 列が更新される。"
        )
        return

    left, right, _ = st.columns([1, 1, 3])
    if left.button("時価を今すぐ更新", key="trigger_refresh"):
        st.session_state["refresh_result"] = sg.trigger_workflow(cfg)
    if right.button("読み込み直す", key="reload_after_refresh",
                    help="更新が終わった頃に押すと最新の時価を読み直す"):
        st.cache_data.clear()
        st.session_state.pop("refresh_result", None)
        st.rerun()

    result = st.session_state.get("refresh_result")
    if result:
        ok, message = result
        (st.success if ok else st.error)(message)


def _render_price_status(
    rows: list[dict], holdings: list[pf.Holding], cfg: sg.StorageConfig | None
) -> None:
    """ライブ時価が取れないときに、保存時価の鮮度と更新導線を出す。

    取れない原因は通信不可のほか API 仕様変更や IP 制限もありうる
    （2026-09-02：yfinance のキーが camelCase 化して全滅／クラウドは Yahoo が 401）。
    保存時価が効いたかは Holding から見る（rows の生値には pandas の "nan" が混ざる）。
    """
    saved_count = sum(1 for h in holdings if h.price_asof)
    if saved_count:
        asof_dates = sorted({h.price_asof for h in holdings if h.price_asof})
        days = pu.stale_days(rows, date.today())
        age = "今日" if days == 0 else f"{days}日前" if days else ""
        st.info(
            f"保存された時価で評価しています（{asof_dates[-1]} 時点"
            + (f"・{age}" if age else "")
            + f"・{saved_count}/{len(holdings)}銘柄）。"
        )
    else:
        has_price_column = any("price" in r for r in rows)
        st.warning(
            "時価が無いため取得単価で評価しています（含み損益は0になる）。"
            + (
                "price 列はあるが有効な値が入っていない。"
                if has_price_column
                else "price 列が無い＝取込データが古い。"
            )
        )
    _render_price_refresh(cfg)


def _render_simple_allocation(
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
    pie_df = pd.DataFrame({axis: labels, "構成比": list(alloc.values())})
    fig = px.pie(pie_df, names=axis, values="構成比", title="現在の構成比")
    left.plotly_chart(fig, width="stretch")

    table_df = pd.DataFrame(
        {axis: labels, "現在%": [round(v, 1) for v in alloc.values()]}
    ).sort_values("現在%", ascending=False)
    show_table(table_df, right, order_key=f"cols_alloc_{axis}", cfg=cfg)


def _merge_uploaded(
    existing: list[dict], uploaded_text: str, src: str
) -> tuple[list[dict], str]:
    """アップロードCSVを既存データへマージする（分類を保つ）。

    CLI の import_holdings.py と同じ merge_holdings を使い、既存の
    purpose/asset_class を残したまま株数・取得単価・名称だけ更新する。
    source は行ごとの値を尊重し、(ticker, source) 単位でマージする
    ＝同一銘柄を複数の証券会社で持っていても片方が消えない。
    """
    uploaded_rows = dataio.parse_holdings_csv(uploaded_text)
    by_source: dict[str, list[dict]] = {}
    for row in uploaded_rows:
        by_source.setdefault(str(row.get("source", "") or "").strip(), []).append(row)

    merged = existing
    for source, group in by_source.items():
        merged = dataio.merge_holdings(merged, group, source=source)
    return merged, f"{src} ＋ アップロード（マージ）"


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
    for col in ("shares", "cost_per_share", "div_per_share", "price"):
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
            "market": st.column_config.SelectboxColumn("上場市場", options=["jp", "us"]),
            "div_per_share": st.column_config.NumberColumn("1株配当", min_value=0.0, format="%,.2f"),
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
            st.cache_data.clear()  # 銘柄構成が変わるため取得済みの時価等を捨てる
            st.success(message)
            st.rerun()
        else:
            st.error(message)
    right.caption(f"保存先：{cfg.owner}/{cfg.repo}/{cfg.path}（{cfg.branch}）")
    if right.button("保存先への接続を確認", key="check_storage"):
        ok, message = sg.check(cfg)
        (right.success if ok else right.error)(message)


def _render_birth_date_input(cfg: sg.StorageConfig | None = None) -> date | None:
    """サイドバーで生年月日を入力・保存し、現在の設定値を返す。

    保存先があれば private repo に置くので、端末をまたいで残る。無ければ従来どおり
    ローカルファイル（Streamlit Cloud はコンテナが揮発するため再起動で消える）。

    読み込みは1セッションに1回だけ（再実行のたびに API を叩かない）。
    """
    st.sidebar.subheader("シミュレーション設定")
    if BIRTH_DATE_STATE not in st.session_state:
        st.session_state[BIRTH_DATE_STATE] = load_birth_date(cfg)
    saved = st.session_state[BIRTH_DATE_STATE]

    birth = st.sidebar.date_input(
        "生年月日",
        value=saved or date(1980, 1, 1),
        min_value=date(1930, 1, 1),
        max_value=date.today(),
        format="YYYY/MM/DD",
        help="年齢を自動計算してシミュレーションの期間に使う。"
             + ("保存先（private repo）に保存する。" if cfg else "ローカルにのみ保存する。"),
    )

    if saved is None:
        st.sidebar.caption("未保存。保存すると次回から自動で読み込む。")
    elif birth != saved:
        st.sidebar.caption(f"保存済み：{saved}（変更後は保存を押す）")

    if st.sidebar.button("生年月日を保存"):
        ok, message = save_birth_date(birth, cfg)
        if ok:
            st.session_state[BIRTH_DATE_STATE] = birth
            st.sidebar.success(message)
        else:
            st.sidebar.warning(message)
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
    current_annual_dividend: float, current_yield: float, years: int, target_age: int,
    tax_rate: float,
) -> None:
    """配当CFタブ：目標（月6〜10万）への到達見込み。

    tax_rate はいまの保有の口座構成から出した実効税率。一律 20.315% で見積もると、
    NISA 分が非課税である実態を反映できず手取りを過小評価する。
    """
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
            rows, src = _merge_uploaded(rows, uploaded_text, src)
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

    tickers = [str(r["ticker"]).strip() for r in rows]
    us_tickers = {str(r["ticker"]).strip() for r in rows if str(r.get("market", "")).strip() == "us"}
    use_live = st.sidebar.checkbox("時価を yfinance から取得", value=True)

    birth_date = _render_birth_date_input(cfg)

    fx_rate = cached_fx_rate() if use_live else None
    price_map = cached_prices(tuple(tickers)) if use_live else {}
    if use_live:
        price_map = pr.convert_us_values_to_jpy(price_map, us_tickers, fx_rate)
    holdings = pf.build_holdings(rows, price_map)

    if not price_map:
        _render_price_status(rows, holdings, cfg)

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
        "集計軸", ["資産クラス", "商品種別", "上場市場", "口座区分"],
        horizontal=True, key="alloc_axis",
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
        show_table(drift_df, right, order_key="cols_drift", cfg=cfg)
    elif axis == "商品種別":
        _render_simple_allocation(
            axis, pf.allocation_by_sector(holdings), {}, left, right, cfg
        )
        st.caption("holdings.csv の sector 列。業種（電気機器・銀行 等）ではなく商品種別。")
    elif axis == "口座区分":
        _render_simple_allocation(
            axis, pf.allocation_by_account(holdings), ACCOUNT_LABELS, left, right, cfg
        )
        st.caption(
            "特定以外は配当の国内課税（20.315%）が非課税。"
            "ただし米国株はNISAでも現地で10%が源泉徴収される（外国税額控除が使えず取り戻せない）。"
        )
    else:
        _render_simple_allocation(
            axis, pf.allocation_by_market_region(holdings), MARKET_LABELS, left, right, cfg
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
    show_table(sector_df, s_col)

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
            _merged_row(group, market, price_map, div_map, pre_tax, tax_mode)
            for group in pf.group_by_ticker(holdings).values()
        ]
    )
    show_table(table, decimals={"損益率%": 2, "構成比%": 1},
               order_key="cols_holdings", cfg=cfg)

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
            # 銘柄別テーブルと同じく、口座で分かれた保有は1行にまとめて見せる
            purpose_df = pd.DataFrame(
                [
                    {
                        "銘柄": rows_of[0].ticker,
                        "名称": rows_of[0].name,
                        "口座": _account_summary(rows_of),
                        "株数": sum(h.shares for h in rows_of),
                        "取得単価": pf.merged_cost_per_share(rows_of),
                        "評価額": round(sum(h.market_value for h in rows_of)),
                        "含み損益": round(sum(h.gain for h in rows_of)),
                    }
                    for rows_of in pf.group_by_ticker(group).values()
                ]
            )
            show_table(purpose_df, order_key=f"cols_purpose_{key or 'none'}", cfg=cfg)

    # --- 保有データの編集（普段の更新はここで完結させる） ---
    _render_holdings_editor(rows, sha, cfg)

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
            tax_rate=dv.effective_tax_rate(holdings, div_map),
        )
    with tab_bt:
        _render_backtest_tab()


if __name__ == "__main__":
    main()
