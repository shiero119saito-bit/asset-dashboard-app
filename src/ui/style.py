"""画面全体のCSS。

KPI帯は Streamlit の st.metric では1画面に6項目を置く前提の余白にならないため、
行間・ラベル・補足の縦寸法を自前で決める（枠は6枚のカードではなく帯全体に1つ）。
"""
from __future__ import annotations

APP_STYLE = """
<style>
/* タイトルと副題を1行に並べる */
.app-title { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
             margin: 0 0 10px; }
/* h1 は Streamlit 側の見出し処理と競合するため span で組む */
.app-title .name { font-size: 2.1rem; font-weight: 700; line-height: 1.2;
                   letter-spacing: .01em; }
.app-title .sub { font-size: 0.9rem; opacity: 0.6; }

/* タブはスクロールしても操作できるよう上端に固定する（Streamlit のヘッダ分だけ下げる） */
/* タブはスクロールしても操作できるよう上端に貼り付ける。
   実DOMは stTabs > div > div:first-child がタブ一覧で、その親（stTabs直下のdiv）が
   タブ本文を含む高さを持つ。1階層ずれると sticky は無効になる（実測で確認）。
   スクロール容器は section[data-testid="stMain"] なので top は0でよい。 */
div[data-testid="stTabs"] > div > div:first-child {
  /* top はヘッダ帯の高さ（実測60px）。0にするとヘッダの下に隠れる */
  position: sticky; top: 3.75rem; z-index: 999;
  background: #ffffff; padding-top: 2px;
  border-bottom: 1px solid rgba(128,128,128,.25);
}
/* 入れ子のタブ（シミュレーション・用途別）は固定しない＝主タブと重なるため */
div[data-testid="stTabs"] div[data-testid="stTabs"] > div > div:first-child {
  position: static; background: transparent; border-bottom: 0;
}

.kpi { padding: 2px 2px 4px; }
/* 項目名は藍で立てる。数字の羅列から見出しを拾えるようにする */
.kpi-label { font-size: 0.78rem; color: #35507e; font-weight: 600;
             line-height: 1.2; margin-bottom: 1px; }
.kpi-divider { border-left: 1px solid rgba(128,128,128,.28); padding-left: 12px; }
.kpi-hr { border: 0; border-top: 1px solid rgba(128,128,128,.28); margin: 8px 0; }
.kpi-main { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; line-height: 1.1; }
.kpi-value { font-size: 1.65rem; font-weight: 600; letter-spacing: -0.01em;
             font-variant-numeric: tabular-nums; }
.kpi-side { font-size: 0.82rem; opacity: 0.65; font-variant-numeric: tabular-nums; }
.kpi-side.up { color: #1f7a5a; opacity: 1; }
.kpi-side.down { color: #a63a4a; opacity: 1; }
.kpi-sub { font-size: 0.76rem; opacity: 0.6; line-height: 1.35;
           font-variant-numeric: tabular-nums; }
.kpi-track { height: 4px; border-radius: 2px; background: rgba(128,128,128,.22);
             margin: 4px 0 3px; overflow: hidden; }
.kpi-track > span { display: block; height: 100%; background: #b8871f; }
/* Cloud はダークテーマで開かれる。暗い地では緑/臙脂が沈むので明度を上げる */
@media (prefers-color-scheme: dark) {
  .kpi-side.up { color: #4bbd91; }
  .kpi-side.down { color: #e0798a; }
  .kpi-track > span { background: #d7a94a; }
  .kpi-label { color: #7ea3dd; }
  /* Streamlit のダークテーマ既定色。--background-color は定義されていない（実測） */
  div[data-testid="stTabs"] > div > div:first-child { background: #0e1117; }
  div[data-testid="stTabs"] div[data-testid="stTabs"] > div > div:first-child {
    background: transparent;
  }
}
</style>
"""
