"""画面全体のCSS。

KPI帯は Streamlit の st.metric では1画面に6項目を置く前提の余白にならないため、
行間・ラベル・補足の縦寸法を自前で決める（枠は6枚のカードではなく帯全体に1つ）。

**明暗は `light-dark()` で切り替える**（2026-09-08 修正）。
以前は `@media (prefers-color-scheme: dark)` を使っていたが、これはOS/ブラウザの設定であって
アプリのテーマではない。両者が食い違うと、テーマに追従する文字色と、OSに追従するタブ帯の
背景が同色になり**タブの文字が消える**（実際に発生。ダークテーマのインスタンスで再現済み）。

`light-dark()` は要素に効いている `color-scheme` を見る。Streamlit は `.stApp` に
`color-scheme: light|dark` を設定していて子孫へ継承されるため、**アプリのテーマにそのまま追従する**
（実測：ダークテーマ配下で `light-dark(#ffffff, #0e1117)` → rgb(14,17,23)＝アプリ背景と一致）。

`st.context.theme` は使わない。config でテーマを固定していても**ブラウザ側の設定**を返すため、
「アプリはダーク・context は light」になり判定に使えない（実測で確認）。
"""
from __future__ import annotations

# 明暗のペア。左＝ライト / 右＝ダーク。
# ダーク側は、暗い地で緑・臙脂・藍が沈むぶん明度を上げてある
_TAB_BAR_BG = ("#ffffff", "#0e1117")   # ダーク側は Streamlit のダークテーマ既定色
_KPI_LABEL = ("#35507e", "#7ea3dd")
_KPI_UP = ("#1f7a5a", "#4bbd91")
_KPI_DOWN = ("#a63a4a", "#e0798a")
_KPI_BAR = ("#b8871f", "#d7a94a")


def _pair(light: str, dark: str) -> str:
    """`light-dark()` 宣言を、非対応ブラウザ向けのライト値付きで組み立てる。

    2行書くのは、`light-dark()` を解釈できないブラウザが2行目を捨てて1行目を使うため
    （その場合の暗所対応は末尾の @supports フォールバックが引き受ける）。
    """
    return f"{light}; background-color: light-dark({light}, {dark})"


APP_STYLE = f"""
<style>
/* タイトルと副題を1行に並べる */
.app-title {{ display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
             margin: 0 0 10px; }}
/* h1 は Streamlit 側の見出し処理と競合するため span で組む */
.app-title .name {{ font-size: 2.1rem; font-weight: 700; line-height: 1.2;
                   letter-spacing: .01em; }}
.app-title .sub {{ font-size: 0.9rem; opacity: 0.6; }}

/* タブはスクロールしても操作できるよう上端に貼り付ける。
   実DOMは stTabs > div > div:first-child がタブ一覧で、その親（stTabs直下のdiv）が
   タブ本文を含む高さを持つ。1階層ずれると sticky は無効になる（実測で確認）。
   スクロール容器は section[data-testid="stMain"] なので top は0でよい。 */
div[data-testid="stTabs"] > div > div:first-child {{
  /* top はヘッダ帯の高さ（実測60px）。0にするとヘッダの下に隠れる */
  position: sticky; top: 3.75rem; z-index: 999;
  padding-top: 2px;
  border-bottom: 1px solid rgba(128,128,128,.25);
  /* 地はアプリのテーマに追従させる。ここをOSの設定で塗ると文字が消える */
  background-color: {_pair(*_TAB_BAR_BG)};
}}
/* 入れ子のタブ（用途別）は固定しない＝主タブと重なるため。地も塗らない */
div[data-testid="stTabs"] div[data-testid="stTabs"] > div > div:first-child {{
  position: static; background-color: transparent; border-bottom: 0;
}}

.kpi {{ padding: 2px 2px 4px; }}
/* 項目名は藍で立てる。数字の羅列から見出しを拾えるようにする */
.kpi-label {{ font-size: 0.78rem; font-weight: 600; line-height: 1.2; margin-bottom: 1px;
             color: {_KPI_LABEL[0]}; color: light-dark({_KPI_LABEL[0]}, {_KPI_LABEL[1]}); }}
.kpi-divider {{ border-left: 1px solid rgba(128,128,128,.28); padding-left: 12px; }}
.kpi-hr {{ border: 0; border-top: 1px solid rgba(128,128,128,.28); margin: 8px 0; }}
.kpi-main {{ display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; line-height: 1.1; }}
.kpi-value {{ font-size: 1.65rem; font-weight: 600; letter-spacing: -0.01em;
             font-variant-numeric: tabular-nums; }}
.kpi-side {{ font-size: 0.82rem; opacity: 0.65; font-variant-numeric: tabular-nums; }}
.kpi-side.up {{ opacity: 1; color: {_KPI_UP[0]}; color: light-dark({_KPI_UP[0]}, {_KPI_UP[1]}); }}
.kpi-side.down {{ opacity: 1; color: {_KPI_DOWN[0]};
                 color: light-dark({_KPI_DOWN[0]}, {_KPI_DOWN[1]}); }}
.kpi-sub {{ font-size: 0.76rem; opacity: 0.6; line-height: 1.35;
           font-variant-numeric: tabular-nums; }}
.kpi-track {{ height: 4px; border-radius: 2px; background: rgba(128,128,128,.22);
             margin: 4px 0 3px; overflow: hidden; }}
.kpi-track > span {{ display: block; height: 100%;
                    background-color: {_pair(*_KPI_BAR)}; }}

/* `light-dark()` を解釈できない古いブラウザ向けの保険。
   この場合だけOSの設定に頼る（アプリのテーマは知りようがないため次善策）。
   対応ブラウザではこのブロック自体が無効になるので、上の指定を上書きしない */
@supports not (color: light-dark(#fff, #000)) {{
  @media (prefers-color-scheme: dark) {{
    div[data-testid="stTabs"] > div > div:first-child {{ background-color: {_TAB_BAR_BG[1]}; }}
    div[data-testid="stTabs"] div[data-testid="stTabs"] > div > div:first-child {{
      background-color: transparent;
    }}
    .kpi-label {{ color: {_KPI_LABEL[1]}; }}
    .kpi-side.up {{ color: {_KPI_UP[1]}; }}
    .kpi-side.down {{ color: {_KPI_DOWN[1]}; }}
    .kpi-track > span {{ background-color: {_KPI_BAR[1]}; }}
  }}
}}
</style>
"""
