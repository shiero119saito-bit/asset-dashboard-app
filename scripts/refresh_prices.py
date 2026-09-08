"""時価・配当を更新するCLI（CSVファイルを介さず保存先へ直接反映する）。

使い方:
    python scripts/refresh_prices.py              # private repo の holdings.csv の時価を更新
    python scripts/refresh_prices.py --dividends  # 配当（div_annual）と権利確定月（div_months）を更新
    python scripts/refresh_prices.py --local      # data/holdings.csv を更新（GitHub Actions・オフライン用）
    python scripts/refresh_prices.py --dry-run    # 取得するだけで書き込まない

なぜこれが要るか：
Streamlit Cloud からは Yahoo Finance が HTTP 401 を返すため、アプリ自身は時価を取得できない。
そこで「取得できる場所」（自宅PC・GitHub Actions）で取得して保存先へ書き込み、
アプリはその値を読むだけにする。従来は間にCSVの手渡し（DL→アップロード）が挟まっていたが、
storage.py で保存先へ直接書けるため不要になった。

時価と配当を別モードにしているのは更新頻度が違うため。時価は平日2回（引け後）だが、
配当と権利確定月は日次で動く値ではないので週1で足りる。同じジョブで毎回引くと
1銘柄あたりのリクエストが3倍になり、平日の時価更新まで重くなる。

設定（--local 以外で必要）は次の順に探す：
    1. 環境変数 ASSET_STORAGE_TOKEN / _OWNER / _REPO / _PATH / _BRANCH
    2. 400_Asset-management/.streamlit/secrets.toml の [storage]（Streamlit のローカル設定と共用・gitignore 済み）
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import dataio  # noqa: E402
import fundprices as fp  # noqa: E402
import prices as pr  # noqa: E402
import pricing_update as pu  # noqa: E402
import storage as sg  # noqa: E402

HOLDINGS_CSV = os.path.join(ROOT, "data", "holdings.csv")
LOCAL_SECRETS = os.path.join(ROOT, ".streamlit", "secrets.toml")

ENV_PREFIX = "ASSET_STORAGE_"


def _config_from_env() -> dict | None:
    """環境変数から保存先設定を作る。token/owner/repo が揃わなければ None。"""
    raw = {
        key.lower(): os.environ[ENV_PREFIX + key]
        for key in ("TOKEN", "OWNER", "REPO", "PATH", "BRANCH")
        if os.environ.get(ENV_PREFIX + key)
    }
    return raw or None


def _config_from_secrets_file(path: str) -> dict | None:
    """ローカルの secrets.toml から [storage] を読む。無ければ None。

    Streamlit がローカル実行で読むのと同じファイル＝設定を二重に持たせない。
    """
    if not os.path.exists(path):
        return None
    try:
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f).get("storage") or None
    except Exception:
        # 壊れた TOML でも落とさず「未設定」として扱い、案内文へ倒す
        return None


def resolve_config() -> sg.StorageConfig | None:
    """保存先設定を環境変数 → ローカル secrets.toml の順に解決する。"""
    return sg.build_config(_config_from_env() or _config_from_secrets_file(LOCAL_SECRETS))


def _text(row: dict, key: str) -> str:
    """行の値を文字列で取り出す。pandas の NaN は空文字にする。"""
    value = row.get(key)
    if value is None:
        return ""
    s = str(value).strip()
    return "" if s.lower() == "nan" else s


def fetch_price_map(rows: list[dict]) -> tuple[dict[str, float], int]:
    """上場銘柄の時価を引く。(price_map（円建て）, yfinance 非対応でスキップした件数)。

    **必ず円建てにして返す**のがこの関数の責務。yfinance は米国銘柄をドル建てで返すため、
    そのまま price 列へ書くと評価額が約1/150になる（2026-09-05 に VYM/SPYD で実害が出た。
    17株のVYMが2,792円と表示されていた）。app.py 側のライブ取得と同じ
    `pr.convert_us_values_to_jpy` を通し、為替が取れないときは米国銘柄をキーごと落とす
    ＝「ドル建ての値が price 列に入る」ことが構造的に起きないようにする。
    """
    tickers = [str(r.get("ticker", "")).strip() for r in rows]
    fetchable = [t for t in tickers if t and pr.is_fetchable(t)]
    price_map = pr.fetch_prices(fetchable)

    us_tickers = {
        str(r.get("ticker", "")).strip() for r in rows if _text(r, "market") == "us"
    }
    if us_tickers & price_map.keys():
        fx_rate = pr.fetch_fx_rate()
        if fx_rate is None:
            print("為替レートを取得できないため、米国銘柄の時価は更新しません。", file=sys.stderr)
        price_map = pr.convert_us_values_to_jpy(price_map, us_tickers, fx_rate)

    return price_map, len(tickers) - len(fetchable)


def fund_codes(rows: list[dict]) -> dict[str, tuple[str, str]]:
    """投資信託の {ticker: (isin, 協会ファンドコード)} を返す。両方揃っている行のみ。"""
    return {
        str(r.get("ticker", "")).strip(): (_text(r, "isin"), _text(r, "assoc_fund_cd"))
        for r in rows
        if _text(r, "isin") and _text(r, "assoc_fund_cd")
    }


def compute_updates(rows: list[dict]) -> tuple[list[dict], int, bool]:
    """時価を取得して行に反映する。(更新後の行, 更新件数, 取得できたか) を返す。

    上場銘柄は yfinance、投資信託は投資信託協会の基準価額CSVから引く。
    「取得できたか」を分けて返すのは、全銘柄で失敗した場合と値動きが無かった場合を
    呼び出し側が区別するため。前者は異常（IP制限・API仕様変更）なので失敗として扱う。
    """
    price_map, skipped = fetch_price_map(rows)
    listed_count = len(price_map)

    funds = fund_codes(rows)
    fund_map = fundprices_for(funds)
    price_map = {**price_map, **fund_map}

    updated_rows, updated = pu.apply_prices(rows, price_map, date.today())
    # 行数と price_map の件数を引き算してはいけない。同じ銘柄を複数の証券会社で持つと
    # 1つの price エントリが複数行を更新するため、取れているのに「取得できず」と出る
    missing = sum(1 for r in rows if str(r.get("ticker", "")).strip() not in price_map)
    print(
        f"時価を更新：{updated}件 / 全{len(rows)}件"
        f"（上場={listed_count}件・投信={len(fund_map)}件・取得できず={missing}件）"
    )
    if skipped and not funds:
        print(f"※ yfinance 非対応が {skipped}件。投信なら isin/assoc_fund_cd を設定すると取得できます。")

    fetched = bool(price_map)
    if not fetched:
        print(
            "1銘柄も時価を取得できませんでした。"
            "Yahoo Finance に拒否されている（IP制限）か、yfinance の仕様変更が疑われます。",
            file=sys.stderr,
        )
    return updated_rows, updated, fetched


def fundprices_for(funds: dict[str, tuple[str, str]]) -> dict[str, float]:
    """投資信託の基準価額を引く（テストで差し替えやすいよう1段挟む）。"""
    return fp.fetch_navs(funds) if funds else {}


def fund_dividends_for(funds: dict[str, tuple[str, str]]) -> dict[str, float]:
    """投資信託の年間分配金を引く（同上）。

    投資信託は yfinance に存在せず分配金が丸ごと欠落する（楽天・SCHD で実害）。
    基準価額と同じ協会CSVから直近1年の分配金を取る。
    """
    return fp.fetch_annual_dividends(funds) if funds else {}


def fetch_dividend_map(rows: list[dict]) -> dict[str, float]:
    """上場銘柄＋投資信託の年間配当（1株/1口あたり）を **円建てで** 返す。

    時価と同じく、米国銘柄は `pr.convert_us_values_to_jpy` を通してから返すのが責務。
    ドル建てのまま div_annual に書くと配当が約1/150になり、利回り・55歳設計の逆算まで
    まとめて狂う（時価で同じ事故を踏んでいる。2026-09-05）。為替が取れないときは
    米国銘柄をキーごと落とす＝ドル建ての値が列に入ることが構造的に起きないようにする。
    """
    tickers = [str(r.get("ticker", "")).strip() for r in rows]
    fetchable = [t for t in tickers if t and pr.is_fetchable(t)]
    div_map = pr.fetch_dividends(fetchable)

    us_tickers = {
        str(r.get("ticker", "")).strip() for r in rows if _text(r, "market") == "us"
    }
    if us_tickers & div_map.keys():
        fx_rate = pr.fetch_fx_rate()
        if fx_rate is None:
            print("為替レートを取得できないため、米国銘柄の配当は更新しません。", file=sys.stderr)
        div_map = pr.convert_us_values_to_jpy(div_map, us_tickers, fx_rate)

    return {**div_map, **fund_dividends_for(fund_codes(rows))}


def fetch_months_map(rows: list[dict]) -> dict[str, list[int]]:
    """権利確定月を引く。投資信託は yfinance に無いため対象外（月不明として集計される）。"""
    tickers = [str(r.get("ticker", "")).strip() for r in rows]
    return pr.fetch_dividend_months([t for t in tickers if t and pr.is_fetchable(t)])


def compute_dividend_updates(rows: list[dict]) -> tuple[list[dict], int, bool]:
    """配当と権利確定月を取得して行に反映する。(更新後の行, 更新件数, 取得できたか)。

    div_per_share（手入力）には触れず div_annual / div_months / div_asof だけを書く。
    """
    div_map = fetch_dividend_map(rows)
    months_map = fetch_months_map(rows)

    updated_rows, updated = pu.apply_dividends(rows, div_map, months_map, date.today())
    missing = sum(1 for r in rows if str(r.get("ticker", "")).strip() not in div_map)
    print(
        f"配当を更新：{updated}件 / 全{len(rows)}件"
        f"（配当={len(div_map)}件・権利確定月={len(months_map)}件・取得できず={missing}件）"
    )

    fetched = bool(div_map)
    if not fetched:
        print(
            "1銘柄も配当を取得できませんでした。"
            "Yahoo Finance に拒否されている（IP制限）か、yfinance の仕様変更が疑われます。",
            file=sys.stderr,
        )
    return updated_rows, updated, fetched


def updater_for(dividends: bool):
    """モードに応じた compute 関数を返す（run_local / run_storage で分岐を持たない）。"""
    return compute_dividend_updates if dividends else compute_updates


def run_local(path: str, dry_run: bool, dividends: bool = False) -> int:
    """ローカルCSVを読み書きする（GitHub Actions はこちらを使い、git 側でコミットする）。"""
    if not os.path.exists(path):
        print(f"holdings.csv が見つかりません：{path}", file=sys.stderr)
        return 1
    with open(path, "r", encoding="utf-8-sig") as f:
        before = f.read()

    rows = dataio.parse_holdings_csv(before)
    if not rows:
        print(f"holdings.csv が空です：{path}", file=sys.stderr)
        return 1

    rows, updated, fetched = updater_for(dividends)(rows)
    after = dataio.serialize_holdings_csv(rows)

    if dry_run:
        print("--dry-run のため書き込みません。")
        return 0 if fetched else 1
    if updated == 0:
        print("変更がないため書き込みません。")
        return 0 if fetched else 1

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(after)
    print(f"書込先：{path}")
    return 0


def run_storage(dry_run: bool, dividends: bool = False) -> int:
    """private repo の holdings.csv を直接更新する（CSVファイルを作らない）。"""
    cfg = resolve_config()
    if cfg is None:
        print(
            "保存先が未設定です。環境変数 ASSET_STORAGE_TOKEN/_OWNER/_REPO を設定するか、\n"
            f"{LOCAL_SECRETS} に [storage] を書いてください（手順は docs/04_deploy.md）。\n"
            "ローカルCSVだけ更新する場合は --local を付けてください。",
            file=sys.stderr,
        )
        return 1

    text, sha = sg.load(cfg)
    if not text:
        ok, message = sg.check(cfg)
        print(f"保存先から読み込めませんでした。{message}", file=sys.stderr)
        return 1

    rows = dataio.parse_holdings_csv(text)
    rows, updated, fetched = updater_for(dividends)(rows)
    after = dataio.serialize_holdings_csv(rows)

    if dry_run:
        print("--dry-run のため書き込みません。")
        return 0 if fetched else 1
    if updated == 0:
        print("変更がないためコミットしません。")
        return 0 if fetched else 1

    what = "dividends" if dividends else "prices"
    ok, message = sg.save(cfg, after, sha, f"refresh {what} ({date.today().isoformat()})")
    print(message)
    if not ok:
        return 1
    print(f"保存先：{cfg.owner}/{cfg.repo}/{cfg.path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="保有銘柄の時価（price列）または配当（div_annual / div_months）を更新する"
    )
    parser.add_argument(
        "--local", action="store_true",
        help="保存先ではなく data/holdings.csv を更新する（GitHub Actions・オフライン用）",
    )
    parser.add_argument("--file", default=HOLDINGS_CSV, help="--local 時の対象CSVパス")
    parser.add_argument("--dry-run", action="store_true", help="取得のみ行い書き込まない")
    parser.add_argument(
        "--dividends", action="store_true",
        help="時価ではなく配当と権利確定月を更新する（手入力の div_per_share には触れない）",
    )
    args = parser.parse_args()

    if args.local:
        return run_local(args.file, args.dry_run, args.dividends)
    return run_storage(args.dry_run, args.dividends)


if __name__ == "__main__":
    sys.exit(main())
