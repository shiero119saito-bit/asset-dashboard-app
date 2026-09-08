"""データ取込・時価の状態表示・生年月日入力（main() の前段で使う導線）。"""
from __future__ import annotations

from datetime import date

import streamlit as st

import dataio
import portfolio as pf
import pricing_update as pu
import storage as sg

from ui.appdata import load_birth_date, save_birth_date
from ui.constants import BIRTH_DATE_STATE


def merge_uploaded(
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


# この日数を超えて更新されていなければ警告に格上げする。時価は平日2回・配当は週1で
# 回るので、1週間以上動いていなければ定期実行が壊れている
STALE_LIMIT_DAYS = 7


def _age_text(days: int | None) -> str:
    """経過日数を短い日本語にする。日付が無ければ空文字。"""
    if days is None:
        return ""
    return "今日" if days == 0 else f"{days}日前"


def render_refresh_buttons(cfg: sg.StorageConfig | None) -> None:
    """更新導線。保存先があれば GitHub Actions に依頼するボタンを出す。

    アプリ自身は Yahoo から取得できない（Streamlit Cloud の IP が 401 で弾かれる）ため、
    取得できる場所＝Actions に肩代わりさせる。押した時だけ起動する（自動起動にしない）：
    Streamlit は操作のたびにスクリプト全体を再実行するため、自動にすると画面を触るたびに
    Actions が走ってしまう。
    """
    if cfg is None:
        st.caption(
            "PCで `python 400_Asset-management/scripts/refresh_prices.py --local`"
            "（配当は `--dividends` を付ける）を実行すると保存値が更新される。"
        )
        return

    with st.expander("データを更新する", expanded=False):
        left, mid, right = st.columns([1, 1, 1])
        if left.button("時価を今すぐ更新", key="trigger_refresh"):
            st.session_state["refresh_result"] = sg.trigger_workflow(cfg)
        if mid.button("配当を今すぐ更新", key="trigger_dividends",
                      help="配当と権利確定月は週1で自動更新される。増配の反映を急ぐときに押す"):
            st.session_state["refresh_result"] = sg.trigger_workflow(
                cfg, "refresh-dividends.yml"
            )
        if right.button("読み込み直す", key="reload_after_refresh",
                        help="更新が終わった頃に押すと最新の保存値を読み直す"):
            st.cache_data.clear()
            st.session_state.pop("refresh_result", None)
            st.rerun()

        result = st.session_state.get("refresh_result")
        if result:
            ok, message = result
            (st.success if ok else st.error)(message)
        st.caption(
            "更新は GitHub Actions が肩代わりする（数十秒）。終わった頃に「読み込み直す」を押す。"
        )


def render_data_freshness(
    rows: list[dict], holdings: list[pf.Holding], cfg: sg.StorageConfig | None
) -> None:
    """保存されている時価・配当がいつ時点かを示し、更新導線を出す。

    画面は yfinance を呼ばない（保存値を読むだけ）ので、**ここだけが鮮度の手がかり**になる。
    定期実行が黙って止まっても気付けるよう、1週間以上古ければ警告に格上げする。
    保存時価が効いたかは Holding から見る（rows の生値には pandas の "nan" が混ざる）。
    """
    today = date.today()
    priced = sum(1 for h in holdings if h.price_asof)
    price_days = pu.stale_days(rows, today)
    dividend_days = pu.stale_days(rows, today, pu.DIVIDEND_ASOF_COLUMN)
    div_count = len(pu.dividend_map(rows))

    if not priced:
        has_price_column = any("price" in r for r in rows)
        st.warning(
            "時価が無いため取得単価で評価しています（含み損益は0になる）。"
            + (
                "price 列はあるが有効な値が入っていない。"
                if has_price_column
                else "price 列が無い＝取込データが古い。"
            )
        )
        render_refresh_buttons(cfg)
        return

    asof = sorted({h.price_asof for h in holdings if h.price_asof})[-1]
    summary = (
        f"時価：{asof}（{_age_text(price_days)}・{priced}/{len(holdings)}銘柄）"
        + "　／　配当："
        + (f"{_age_text(dividend_days)}（{div_count}銘柄）" if dividend_days is not None
           else "未取得")
    )
    stale = (price_days or 0) > STALE_LIMIT_DAYS or dividend_days is None \
        or dividend_days > STALE_LIMIT_DAYS
    if stale:
        st.warning(summary + "　定期更新が止まっている可能性がある。")
    else:
        st.caption(summary)
    render_refresh_buttons(cfg)


def render_birth_date_input(cfg: sg.StorageConfig | None = None) -> date | None:
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
