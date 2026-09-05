"""時価だけを更新するCLI（CSVファイルを介さず保存先へ直接反映する）。

使い方:
    python scripts/refresh_prices.py            # private repo の holdings.csv を直接更新
    python scripts/refresh_prices.py --local    # data/holdings.csv を更新（GitHub Actions・オフライン用）
    python scripts/refresh_prices.py --dry-run  # 取得するだけで書き込まない

なぜこれが要るか：
Streamlit Cloud からは Yahoo Finance が HTTP 401 を返すため、アプリ自身は時価を取得できない。
そこで「取得できる場所」（自宅PC・GitHub Actions）で取得して保存先へ書き込み、
アプリはその値を読むだけにする。従来は間にCSVの手渡し（DL→アップロード）が挟まっていたが、
storage.py で保存先へ直接書けるため不要になった。

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


def fetch_price_map(rows: list[dict]) -> tuple[dict[str, float], int]:
    """行の ticker から時価を引く。(price_map, yfinance 非対応でスキップした件数)。"""
    tickers = [str(r.get("ticker", "")).strip() for r in rows]
    fetchable = [t for t in tickers if t and pr.is_fetchable(t)]
    return pr.fetch_prices(fetchable), len(tickers) - len(fetchable)


def compute_updates(rows: list[dict]) -> tuple[list[dict], int, bool]:
    """時価を取得して行に反映する。(更新後の行, 更新件数, 取得できたか) を返す。

    「取得できたか」を分けて返すのは、全銘柄で失敗した場合と値動きが無かった場合を
    呼び出し側が区別するため。前者は異常（IP制限・API仕様変更）なので失敗として扱う。
    """
    price_map, skipped = fetch_price_map(rows)
    updated_rows, updated = pu.apply_prices(rows, price_map, date.today())
    print(f"時価を更新：{updated}件 / 全{len(rows)}件（yfinance非対応でスキップ={skipped}件）")

    fetched = bool(price_map) or skipped >= len(rows)
    if not fetched:
        print(
            "1銘柄も時価を取得できませんでした。"
            "Yahoo Finance に拒否されている（IP制限）か、yfinance の仕様変更が疑われます。",
            file=sys.stderr,
        )
    return updated_rows, updated, fetched


def run_local(path: str, dry_run: bool) -> int:
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

    rows, updated, fetched = compute_updates(rows)
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


def run_storage(dry_run: bool) -> int:
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
    rows, updated, fetched = compute_updates(rows)
    after = dataio.serialize_holdings_csv(rows)

    if dry_run:
        print("--dry-run のため書き込みません。")
        return 0 if fetched else 1
    if updated == 0:
        print("変更がないためコミットしません。")
        return 0 if fetched else 1

    ok, message = sg.save(cfg, after, sha, f"refresh prices ({date.today().isoformat()})")
    print(message)
    if not ok:
        return 1
    print(f"保存先：{cfg.owner}/{cfg.repo}/{cfg.path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="保有銘柄の時価（price列）だけを更新する")
    parser.add_argument(
        "--local", action="store_true",
        help="保存先ではなく data/holdings.csv を更新する（GitHub Actions・オフライン用）",
    )
    parser.add_argument("--file", default=HOLDINGS_CSV, help="--local 時の対象CSVパス")
    parser.add_argument("--dry-run", action="store_true", help="取得のみ行い書き込まない")
    args = parser.parse_args()

    if args.local:
        return run_local(args.file, args.dry_run)
    return run_storage(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
