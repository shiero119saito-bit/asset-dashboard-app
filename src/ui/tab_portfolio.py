"""ポートフォリオタブ：リスクの偏りを見る場所。集中度と切り口別の構成比。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import cash as ca
import dividend as dv
import portfolio as pf

from ui.constants import ACCOUNT_LABELS, MARKET_LABELS, PURPOSE_LABELS, SOURCE_LABELS
from ui.widgets import pie, show_table, simple_allocation

# 集計軸の一覧。**定数として外に出しているのはテストが全軸を通すため**
# （ハードコードの範囲でループしていたせいで、後から足した軸が一度も実行されずに緑になっていた）
ALLOCATION_AXES = (
    "資産クラス", "業種", "投資対象地域", "企業規模", "商品種別", "上場市場", "口座区分",
)


def render(holdings, div_map, cash_rows, cfg) -> None:
    """リスクの偏りを見る場所。集中度と切り口別の構成比。"""
    market = pf.total_market(holdings)
    shares = pf.share_by_ticker(holdings)

    st.subheader("集中リスク")
    industry_alloc = pf.allocation_by_industry(pf.jp_stocks_only(holdings))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("銘柄数", f"{len(shares)}")
    c2.metric("最大銘柄比率", f"{max(shares.values()):.1f}%" if shares else "—")
    c3.metric("上位5銘柄", f"{pf.top_n_share(holdings, 5):.1f}%")
    c4.metric("上位10銘柄", f"{pf.top_n_share(holdings, 10):.1f}%")
    c1.caption(f"現金比率 {ca.cash_ratio(market, cash_rows):.1f}%")
    c2.caption(
        f"最大業種 {max(industry_alloc.values()):.1f}%" if industry_alloc else "業種未設定"
    )

    st.subheader("アセットアロケーション")
    _render_allocation_axes(holdings, cfg)

    st.subheader("銘柄別")
    tax_mode = "税込"
    table = pd.DataFrame([
        _merged_row(group, market, div_map, True, tax_mode)
        for group in pf.group_by_ticker(holdings).values()
    ])
    show_table(table, decimals={"損益率%": 2, "構成比%": 1}, order_key="cols_holdings", cfg=cfg)

    st.subheader("日本個別株：高配当・優待")
    by_purpose = pf.jp_dividend_by_purpose(holdings)
    tab_dividend, tab_yutai, tab_unclassified = st.tabs(["高配当", "優待", "未分類"])
    for tab, key in ((tab_dividend, "dividend"), (tab_yutai, "yutai"), (tab_unclassified, "")):
        group = by_purpose.get(key, [])
        with tab:
            if not group:
                st.caption("該当なし")
                continue
            purpose_df = pd.DataFrame([
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
            ])
            show_table(purpose_df, order_key=f"cols_purpose_{key or 'none'}", cfg=cfg)


@st.fragment
def _render_allocation_axes(holdings, cfg) -> None:
    """集計軸を切り替えて構成比を見る（資産クラスだけ目標AAとのズレを出す）。

    fragment＝軸を切り替えても再描画はこのセクションだけで済む（以前は全タブが組み直された）。

    軸名はデータの実態に合わせている。sector 列は業種でなく商品種別、
    market 列は上場市場（投資対象地域ではない）。
    """
    axis = st.radio("集計軸", list(ALLOCATION_AXES), horizontal=True, key="alloc_axis")
    left, right = st.columns([1, 1])

    if axis == "資産クラス":
        alloc = pf.allocation_by_class(holdings)
        drift = pf.allocation_drift(holdings)
        # 資産クラスは4区分固定＝目標AAと突き合わせる軸なので、小さくてもまとめない
        left.plotly_chart(
            pie({pf.ASSET_CLASS_LABELS[ac]: alloc[ac] for ac in pf.ASSET_CLASSES},
                "資産クラス", "現在の構成比", group_small=False),
            width="stretch",
        )
        drift_df = pd.DataFrame({
            "資産クラス": [pf.ASSET_CLASS_LABELS[ac] for ac in pf.ASSET_CLASSES],
            "現在%": [round(alloc[ac], 1) for ac in pf.ASSET_CLASSES],
            "目標%": [pf.TARGET_ALLOCATION[ac] for ac in pf.ASSET_CLASSES],
            "ズレ": [round(drift[ac], 1) for ac in pf.ASSET_CLASSES],
        })
        show_table(drift_df, right, order_key="cols_drift", cfg=cfg)
    elif axis == "業種":
        # ETF・投信が6割を占めるため、既定は個別株のみ＝業種分散が読み取れる状態にする
        jp_only = st.checkbox(
            "日本個別株のみ", value=True, key="alloc_industry_jp_only",
            help="OFFにするとETF・投信/REITも「ETF・投信」「REIT」区分として合算し、全資産で100%になる",
        )
        target = pf.jp_stocks_only(holdings) if jp_only else holdings
        simple_allocation(axis, pf.allocation_by_industry(target), {}, left, right, cfg)
        st.caption(
            "東証33業種（holdings.csv の industry 列）。"
            + (f"日本個別株 {len(pf.group_by_ticker(target))} 銘柄が対象。"
               if jp_only else "ETF・投信は中身を業種に分解せず1区分として扱う。")
        )
    elif axis == "投資対象地域":
        simple_allocation(axis, pf.allocation_by_region(holdings), {}, left, right, cfg)
        unset = pf.allocation_by_region(holdings).get(pf.REGION_UNSET, 0.0)
        st.caption(
            "**投信・ETFは中身で分類する**（東証上場のオルカン＝全世界、S&P500＝米国）。"
            "個別株は上場市場から自動で決まる。下の「上場市場」軸とは別物で、"
            "そちらは東証上場ならすべて日本株に数える。"
            + (f"　未設定が {unset:.1f}% ある（データタブの用途の隣で選べる）。" if unset else "")
        )
    elif axis == "企業規模":
        simple_allocation(axis, pf.allocation_by_size(holdings), {}, left, right, cfg)
        st.caption(
            f"時価総額の区分（大型＝{pf.SIZE_TIERS[0][1] / 1e12:.0f}兆円以上／"
            f"中型＝{pf.SIZE_TIERS[1][1] / 1e8:.0f}億円以上／小型＝それ未満）。"
            "**公式の指数区分ではなく表示上の目安**。ETF・投信は時価総額を取得できないため"
            "「対象外」にまとめている（中身の規模までは分解しない）。"
        )
    elif axis == "商品種別":
        simple_allocation(axis, pf.allocation_by_sector(holdings), {}, left, right, cfg)
        st.caption("holdings.csv の sector 列。業種（電気機器・銀行 等）ではなく商品種別。")
    elif axis == "口座区分":
        simple_allocation(
            axis, pf.allocation_by_account(holdings), ACCOUNT_LABELS, left, right, cfg)
        st.caption(
            "特定以外は配当の国内課税（20.315%）が非課税。"
            "ただし米国株はNISAでも現地で10%が源泉徴収される（外国税額控除が使えず取り戻せない）。"
        )
    else:
        simple_allocation(
            axis, pf.allocation_by_market_region(holdings), MARKET_LABELS, left, right, cfg)
        st.caption(
            "上場市場ベース。東証上場のオルカン・S&P500 ETF/投信は「日本株」に計上される"
            "（投資対象地域ではない）。"
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
        # 保存時価が無い行は取得単価で評価されている（含み損益が0になる）ことを明示する
        "時価": head.price_asof or "取得単価",
        "評価額": round(value),
        "含み損益": round(gain),
        "損益率%": round(gain / cost * 100, 2) if cost else 0.0,
        "構成比%": round(value / total_market * 100, 1) if total_market else 0.0,
        f"年間配当({tax_mode})": round(
            sum(dv.holding_dividend(h, div_map, pre_tax) for h in group)
        ),
    }
