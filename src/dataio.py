"""保有データ・個人設定の入出力ヘルパー（純関数）。

CSV文字列 → rows（list[dict]）の変換、設定JSONの相互変換を担う。
secrets / アップロード / ローカルファイルいずれのソースでも同じパーサを再利用できるよう、
ファイルI/O から分離した純関数として切り出す（読み書きは app.py 側）。
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date

import pandas as pd

# holdings の必須列（最低限これが無いと集計できない）
REQUIRED_COLUMNS = ("ticker", "name", "asset_class", "shares", "cost_per_share")

# holdings.csv の正準列順。CLI（importers.merge）と画面編集の保存で同一形式にする。
# 取込側の importers に依存させないのは、公開デプロイにはダッシュボードに必要な
# モジュールしか置かないため（importers は証券会社CSV取込＝ローカル専用）。
HOLDINGS_COLUMNS = (
    "ticker",
    "name",
    "asset_class",
    "shares",
    "cost_per_share",
    "sector",
    # 業種（東証33業種）。sector（商品種別）とは別軸＝業種分散を見るための列。
    # ETF・投信は「ETF・投信」、REITは「REIT」でまとめる（業種軸でも全資産が100%になる）
    "industry",
    "market",
    "div_per_share",
    # 購入時点の年1株配当。購入時利回り（＝新規投資の効率）を出すために要る。
    # 現在の配当（div_per_share）は増配で動くため、簿価利回りとは別指標になる
    "div_at_purchase",
    # 自動取得の配当データ（refresh_prices.py --dividends が書く）。
    # div_per_share は手入力の上書き値として残し、画面は div_per_share → div_annual の順に使う
    # ＝定期取得が手で直した値を潰さない。div_annual は必ず円建て（米国株は取得時に換算）
    "div_annual",
    "div_months",   # 権利確定月。`3;9` 形式（複数月は ; 区切り）
    "div_asof",     # div_annual / div_months を取得した日付
    "purpose",
    "source",
    # 口座区分。税率が変わる（NISA は国内課税が非課税）ため、同一銘柄でも口座別に行を分ける
    "account",
    "price",
    "price_asof",
    # 投資信託の基準価額取得用（投資信託協会のCSVはこの2つでファンドが特定される）。
    # 上場銘柄では空欄。yfinance に存在しない投信を時価評価するために必要
    "isin",
    "assoc_fund_cd",
)


def parse_holdings_csv(text: str) -> list[dict]:
    """CSV文字列を rows（list[dict]）へ変換する。

    追加列（sector / market / div_per_share）は任意。欠損は呼び出し側（build_holdings）が補完。
    必須列が欠けている場合は ValueError。空入力は空リスト。
    """
    if text is None or text.strip() == "":
        return []
    df = pd.read_csv(io.StringIO(text))
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"必須列が不足しています: {', '.join(missing)}")
    return df.to_dict("records")


def serialize_holdings_csv(rows: list[dict], columns: tuple[str, ...] | None = None) -> str:
    """rows を holdings.csv 形式の文字列にする（parse_holdings_csv の逆）。

    列順は HOLDINGS_COLUMNS に合わせ、CLI が書くファイルと同一形式にする。
    欠損値・NaN は空文字にして「nan」という文字列がCSVに残らないようにする
    （pandas が空欄を float('nan') で読むため、素直に str() すると "nan" になる）。
    """
    cols = columns or HOLDINGS_COLUMNS
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(cols), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({col: _cell(row.get(col)) for col in cols})
    return buf.getvalue()


def _cell(value) -> str:
    """CSVセル1つ分の文字列化。None・NaN・"nan" は空文字に落とす。

    整数値の float は整数として書く（`69991.0` → `69991`）。pandas が数値列を float で
    読むため、素直に str() すると株数や時価に不要な `.0` が付いて読みにくくなる。
    小数を持つ値（取得単価 `1.228729` 等）はそのまま残す。
    """
    if value is None:
        return ""
    if isinstance(value, float):
        if value != value:  # NaN
            return ""
        if value.is_integer():
            return str(int(value))
    s = str(value).strip()
    return "" if s.lower() == "nan" else s


# --- 取込データのマージ（証券会社CSV取込・画面アップロードの両方で使う）---
# 同一銘柄を複数の証券会社・複数の口座区分で保有しうるため、キーは
# (ticker, source, account)。既存の分類済みメタは上書きせず、数量・取得単価・名称だけを
# 最新値で更新する。

REIT_NAME_MARKERS = ("ＲＥＩＴ", "REIT", "リート")

# 口座区分。空欄は特定口座として扱う（課税＝安全側。手入力データが未設定でも損しない）
ACCOUNT_SPECIFIC = "specific"
ACCOUNT_NISA_OLD = "nisa_old"
ACCOUNT_NISA_TSUMITATE = "nisa_tsumitate"
ACCOUNT_NISA_GROWTH = "nisa_growth"
ACCOUNTS = (ACCOUNT_SPECIFIC, ACCOUNT_NISA_OLD, ACCOUNT_NISA_TSUMITATE, ACCOUNT_NISA_GROWTH)

# 再取込で行が口座別に分割されたとき、分割前の行から引き継ぐ分類情報。
# これが無いと purpose や isin（投信の基準価額取得に必要）が分割の瞬間に消える
META_COLUMNS = (
    "asset_class", "sector", "industry", "market", "div_per_share", "div_at_purchase",
    "purpose", "isin", "assoc_fund_cd",
)


def account_from_label(label: str) -> str:
    """証券会社CSVの口座区分表記を内部値へ変換する。

    表記は証券会社・レポート・年度で揺れる（「NISA成長投資枠」「つみたてNISA」
    「特定口座」「特定」等）ため、含まれる語で判定する。判別できないものは特定口座
    ＝課税扱いにする（非課税と誤判定して手取りを過大表示しないため）。

    **新旧の区別は「投資枠」という語で行う**。新NISA（2024〜）は「つみたて投資枠」
    「成長投資枠」と呼び、旧制度は「つみたてNISA」「一般NISA」と呼ぶ。
    「つみたて」の有無だけで判定すると、旧制度の「つみたてNISA」が新しい積立枠に
    混ざってしまう（非課税である点は同じだが、Shiero は新旧を分けて見たい）。
    """
    s = str(label or "").strip()
    if not s:
        return ACCOUNT_SPECIFIC
    if "投資枠" in s:  # 新NISA
        if "つみたて" in s or "積立" in s or "ツミタテ" in s:
            return ACCOUNT_NISA_TSUMITATE
        if "成長" in s:
            return ACCOUNT_NISA_GROWTH
        return ACCOUNT_NISA_OLD  # 「投資枠」だが種別不明。非課税なのは確かなのでNISA側へ
    if "NISA" in s or "ＮＩＳＡ" in s or "ニーサ" in s:
        return ACCOUNT_NISA_OLD  # つみたてNISA・一般NISA（旧制度）
    return ACCOUNT_SPECIFIC


def normalize_account(value) -> str:
    """口座区分を正規化する。空欄・NaN は特定口座扱い。

    キーの一致に使うため、空欄と "specific" を同じものとして扱う必要がある
    （既存行が空欄で取込行が specific だと、別行として二重に増える）。
    """
    s = str(value or "").strip()
    return s if s and s.lower() != "nan" else ACCOUNT_SPECIFIC


def _default_metadata(name: str, hint: dict | None = None) -> dict:
    """新規銘柄の既定分類。名称にREIT系の表記があれば reit、なければ個別株jp_dividendとする。

    hint（=importerが返した行dict自体）に asset_class/sector/market があれば既定値を
    上書きする（投資信託等、importer側で分類済みのケース向け）。既存のrakuten/sbi/esmart
    importerはこれらのキーを含まないため、hintを渡さない呼び出しは従来どおりの挙動になる。

    優待/高配当の別（purpose）は保有動機の主観情報のため対象外＝常に空欄。
    業種（industry）はREITだけ確定できる。個別株は銘柄ごとの調査が要るため空欄＝未分類。
    """
    if any(marker in name for marker in REIT_NAME_MARKERS):
        meta = {"asset_class": "reit", "sector": "REIT", "industry": "REIT",
                "market": "jp", "div_per_share": "", "div_at_purchase": "", "purpose": ""}
    else:
        meta = {"asset_class": "jp_dividend", "sector": "個別株", "industry": "",
                "market": "jp", "div_per_share": "", "div_at_purchase": "", "purpose": ""}
    if hint:
        for key in ("asset_class", "sector", "industry", "market"):
            if hint.get(key):
                meta[key] = hint[key]
    return meta


def _key(ticker: str, source: str, account) -> tuple[str, str, str]:
    return (str(ticker).strip(), str(source).strip(), normalize_account(account))


def _meta_fallback(existing_rows: list[dict]) -> dict[tuple[str, str], dict]:
    """(ticker, source) → 分類済みメタ の対応表。口座別に分割された行の初期値に使う。

    再取込で1行が口座別の複数行に分かれるとき、新しい行は既存キーに一致しないため
    既定分類（新規銘柄扱い）になってしまう。分割前の行が持っていた purpose や isin を
    引き継ぐことで、分類のやり直しを防ぐ。
    """
    table: dict[tuple[str, str], dict] = {}
    for row in existing_rows:
        key = (str(row.get("ticker", "")).strip(), str(row.get("source", "")).strip())
        table.setdefault(key, {col: row.get(col, "") for col in META_COLUMNS})
    return table


def merge_holdings(
    existing_rows: list[dict],
    imported_rows: list[dict],
    source: str,
    replace_source: bool = False,
) -> list[dict]:
    """既存 holdings 行 + 証券会社CSV取込行 → マージ済み holdings 行リスト。

    existing_rows: holdings.csv 由来の行（(ticker, source, account) をキーに分類済みメタを保持）
    imported_rows: importers.rakuten/sbi の parse_*_csv が返す
                    {"ticker", "name", "shares", "cost_per_share"}（+ 任意で "account"）のリスト
    source: 今回の取込元（例 "rakuten"/"sbi"）。同一 (ticker, source, account) のみ更新対象。
    replace_source: **全保有を含むレポート**（楽天「資産合計」等）を取り込むときに True。
        その source の既存行をいったん捨ててからCSVの内容で作り直す。

    replace_source が要る理由：口座区分を持つ前のデータは全て specific で、再取込すると
    同じ保有が NISA 側の新しい行として**追加**されてしまう（キーが違うため）。
    実際に楽天の評価額が2倍になった。CSVが全保有を含むなら、載っていない
    (ticker, account) の組み合わせはもう保有していないので、捨てるのが正しい。

    一部しか含まないレポート（国内株のみ等）で True にすると他の資産が消えるので、
    呼び出し側が明示的に指定する。
    """
    meta_fallback = _meta_fallback(existing_rows)  # 捨てる前に分類を退避する
    kept = (
        [row for row in existing_rows if str(row.get("source", "")).strip() != source.strip()]
        if replace_source
        else existing_rows
    )
    by_key: dict[tuple[str, str, str], dict] = {
        _key(row.get("ticker"), row.get("source", ""), row.get("account")): dict(row)
        for row in kept
    }

    for imported in imported_rows:
        ticker = str(imported["ticker"]).strip()
        account = normalize_account(imported.get("account"))
        k = _key(ticker, source, account)
        if k in by_key:
            merged = by_key[k]
            merged["name"] = imported["name"]
            merged["shares"] = imported["shares"]
            merged["cost_per_share"] = imported["cost_per_share"]
        else:
            # 同じ銘柄が別口座に既にあればその分類を引き継ぐ。無ければ新規銘柄の既定分類
            meta = meta_fallback.get((ticker, source)) or _default_metadata(
                imported["name"], imported
            )
            merged = {
                "ticker": ticker,
                "name": imported["name"],
                "shares": imported["shares"],
                "cost_per_share": imported["cost_per_share"],
                "source": source,
                "account": account,
                **meta,
            }
        merged["account"] = account
        by_key[k] = merged

    return [
        {col: row.get(col, "") for col in HOLDINGS_COLUMNS}
        for row in by_key.values()
    ]


# --- 個人設定（生年月日）。機微情報のため保存先は .gitignore 済み ---

BIRTH_DATE_KEY = "birth_date"


def parse_birth_date(text: str | None) -> date | None:
    """設定JSON文字列から生年月日を取り出す。未設定・壊れていれば None。

    設定ファイルは手で壊れうる（手編集・空ファイル）ため、例外を投げず
    None を返して「未設定」として扱う（呼び出し側は入力を促すだけで済む）。
    """
    if not text or not text.strip():
        return None
    try:
        value = json.loads(text).get(BIRTH_DATE_KEY)
        return date.fromisoformat(value) if value else None
    except (ValueError, TypeError, AttributeError):
        return None


def serialize_birth_date(birth: date, existing: str | None = None) -> str:
    """生年月日を設定JSON文字列にする（ISO 8601）。

    **既存の設定に上書きマージする**。同じファイルに目標額も入っているため、
    生年月日だけを書き出すと目標が消える（1ファイル1設定の前提だった名残）。
    """
    settings = parse_settings(existing)
    settings[BIRTH_DATE_KEY] = birth.isoformat()
    return json.dumps(settings, ensure_ascii=False, indent=2)


# --- 目標額。生年月日と同じ設定JSONに同居させる（保存先は .gitignore 済み） ---

# 既定値は Shiero の戦略（55歳で月6〜10万のCF・資産形成の到達点）に合わせた初期値。
# 画面から変更できる＝ここは「未設定のときの出発点」でしかない
DEFAULT_GOALS = {
    # --- 目標（金額・年齢）。0以下は未設定として既定値に戻す ---
    "target_age": 55.0,                    # セミリタイア設計の基準年齢
    "goal_dividend_annual": 600_000.0,     # 年間配当（税抜）＝月5万
    "goal_dividend_monthly": 50_000.0,     # 月間配当（税抜）
    "goal_withdrawal_monthly": 50_000.0,   # インデックスの取り崩し月額
    "goal_business_monthly": 50_000.0,     # 事業（副業）月額
    "goal_labor_monthly": 50_000.0,        # 労働収入（パート）月額
    "goal_net_worth": 20_000_000.0,        # 総資産
    # --- 前提値（率）。0を有効な入力として受け付ける ---
    "assumed_purchase_yield": 4.0,         # 新規購入の想定利回り（%・額面）
    "assumed_index_return": 5.0,           # インデックスの想定年率（%）
    "assumed_index_monthly": 50_000.0,     # インデックスへの毎月の積立額
    "scenario_growth_low": 0.0,            # 増配シナリオ：保守
    "scenario_growth_mid": 3.0,            # 増配シナリオ：標準
    "scenario_growth_high": 5.0,           # 増配シナリオ：強気
}

# 0 を有効な値として受け付けるキー（増配率0%＝保守シナリオは正当な設定）
ZERO_ALLOWED = {
    "assumed_purchase_yield", "assumed_index_return", "assumed_index_monthly",
    "scenario_growth_low", "scenario_growth_mid", "scenario_growth_high",
}


def parse_settings(text: str | None) -> dict:
    """設定JSON文字列を辞書へ。未設定・壊れていれば空辞書（例外は投げない）。"""
    if not text or not text.strip():
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def parse_goals(text: str | None) -> dict[str, float]:
    """設定JSONから目標額を取り出す。欠けているキーは既定値で補う。"""
    settings = parse_settings(text)
    goals = dict(DEFAULT_GOALS)
    for key in DEFAULT_GOALS:
        try:
            value = float(settings[key])
        except (KeyError, TypeError, ValueError):
            continue
        if value > 0 or (key in ZERO_ALLOWED and value >= 0):
            goals[key] = value
    return goals


def serialize_goals(goals: dict[str, float], existing: str | None = None) -> str:
    """目標額を設定JSON文字列にする。既存の設定（生年月日等）は保つ。"""
    settings = parse_settings(existing)
    for key in DEFAULT_GOALS:
        if key in goals:
            settings[key] = float(goals[key])
    return json.dumps(settings, ensure_ascii=False, indent=2)


def goal_progress(current: float, goal: float) -> float:
    """達成率（%）。目標が0以下なら0。100%を超えてもそのまま返す（超過は隠さない）。"""
    return current / goal * 100.0 if goal > 0 else 0.0
