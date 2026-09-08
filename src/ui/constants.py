"""画面で使うラベル・パス・session_state キー。

ここは値だけを置く（streamlit も業務モジュールも import しない）。
"""
from __future__ import annotations

import os

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"
)
REAL_CSV = os.path.join(DATA_DIR, "holdings.csv")
SAMPLE_CSV = os.path.join(DATA_DIR, "holdings.sample.csv")
SETTINGS_JSON = os.path.join(DATA_DIR, "user_settings.json")  # 生年月日（.gitignore 済み）

# 保有目的。当初は日本個別株の「高配当か優待か」を分ける列だったが、
# インデックス（オルカン等）は資産最大化が目的で配当も優待も目的ではないため growth を足した。
# 空文字は未分類（取込直後の既定値）。
PURPOSE_LABELS_BY_VALUE = {"dividend": "配当", "growth": "資産形成", "yutai": "優待"}
PURPOSE_LABELS = {**PURPOSE_LABELS_BY_VALUE, "": "未分類"}
SOURCE_LABELS = {"rakuten": "楽天", "sbi": "SBI", "": "手入力"}

# 配当セクションの集計対象。値は purpose のフィルタ集合（空タプル＝絞り込みなし）。
# 資産形成（インデックス）の配当が混ざると「配当目的の資産の利回り」が見えないため分ける。
DIVIDEND_SCOPES = {
    "全資産": (),
    "配当目的のみ": ("dividend",),
    "配当＋優待": ("dividend", "yutai"),
}

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

# 設定類は保有データと同じ repo の別ファイルに置く。session_state もローカルファイルも
# ブラウザを閉じる／コンテナが再起動すると消えるため、端末をまたいで残らない
VIEW_SETTINGS_PATH = "view_settings.json"   # 列の並び順
USER_SETTINGS_PATH = "user_settings.json"   # 生年月日・目標額（機微情報。repo は Private）
SNAPSHOTS_PATH = "snapshots.csv"            # 月次の資産スナップショット
HISTORY_PATH = "dividend_history.csv"       # 受取配当の実績
CASH_PATH = "cash.csv"                      # 現金・預金の残高
INCOME_PATH = "income.csv"                  # 事業・労働収入の実績
VIEW_ORDERS_STATE = "view_orders"
BIRTH_DATE_STATE = "birth_date"
GOALS_STATE = "goals"
CASH_STATE = "cash_rows"
SNAPSHOT_STATE = "snapshot_rows"
HISTORY_STATE = "dividend_history_rows"
INCOME_STATE = "income_rows"

# 保存先が未設定の環境（ローカル実行）で使うファイル
CASH_CSV = os.path.join(DATA_DIR, "cash.csv")
INCOME_CSV = os.path.join(DATA_DIR, "income.csv")
SNAPSHOTS_CSV = os.path.join(DATA_DIR, "snapshots.csv")
HISTORY_CSV = os.path.join(DATA_DIR, "dividend_history.csv")
