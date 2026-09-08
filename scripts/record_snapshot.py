"""資産スナップショット（月1行）を記録するCLI。

使い方:
    python scripts/record_snapshot.py            # private repo の snapshots.csv に追記
    python scripts/record_snapshot.py --local    # data/ 配下のCSVに追記（Actions・オフライン用）
    python scripts/record_snapshot.py --dry-run  # 記録内容を表示するだけ

なぜ要るか：
アプリは「現在の評価額」しか持たず、月をまたぐと前月の姿が残らない。推移を見るには
点を積み上げるしかないので、時価更新と同じ場所（GitHub Actions・自宅PC）で1行足す。
**同じ月は上書き**するので、月内に何度動かしても行は増えない。

設定（--local 以外で必要）の探索順は refresh_prices.py と同じ：
    1. 環境変数 ASSET_STORAGE_TOKEN / _OWNER / _REPO / _PATH / _BRANCH
    2. 400_Asset-management/.streamlit/secrets.toml の [storage]
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cash as ca  # noqa: E402
import dataio  # noqa: E402
import fundprices as fp  # noqa: E402
import portfolio as pf  # noqa: E402
import prices as pr  # noqa: E402
import pricing_update as pu  # noqa: E402
import snapshots as sn  # noqa: E402
import storage as sg  # noqa: E402
from refresh_prices import resolve_config  # noqa: E402  設定探索は1か所に持つ

HOLDINGS_CSV = os.path.join(ROOT, "data", "holdings.csv")
SNAPSHOTS_CSV = os.path.join(ROOT, "data", "snapshots.csv")
CASH_CSV = os.path.join(ROOT, "data", "cash.csv")

# 保存先（private repo）でのファイル名。holdings.csv と同じ repo に置く
SNAPSHOT_PATH = "snapshots.csv"
CASH_PATH = "cash.csv"


def dividend_map_from_csv(rows: list[dict]) -> dict[str, float]:
    """CSVから配当マップを作る。**手入力（div_per_share）が自動取得（div_annual）に勝つ**。

    優先順位は画面と同じ `pricing_update.dividend_map` に持たせる（2か所に散らさない）。
    """
    return pu.dividend_map(rows)


def fetch_missing_dividends(rows: list[dict], div_map: dict[str, float]) -> dict[str, float]:
    """配当が保存されていない銘柄だけ yfinance から補う（**円建てに換算して返す**）。

    週1の `refresh_prices.py --dividends` が div_annual を書くので通常は空振りするが、
    取り込んだばかりの銘柄がここで0のまま記録されると推移が欠けるため残す。
    米国銘柄はドル建てで返るため `convert_us_values_to_jpy` を通す
    （為替が取れなければ米国銘柄はキーごと落ちる＝ドル建ての値が混ざらない）。
    """
    missing = [
        t for t in (str(r.get("ticker", "")).strip() for r in rows)
        if t and t not in div_map and pr.is_fetchable(t)
    ]
    if not missing:
        return div_map

    fetched = pr.fetch_dividends(missing)
    # 投資信託は yfinance に無いので、基準価額と同じ協会CSVから分配金を取る
    funds = {
        str(r.get("ticker", "")).strip(): (
            str(r.get("isin", "")).strip(), str(r.get("assoc_fund_cd", "")).strip()
        )
        for r in rows
        if str(r.get("ticker", "")).strip() not in div_map
        and str(r.get("isin", "")).strip() not in ("", "nan")
        and str(r.get("assoc_fund_cd", "")).strip() not in ("", "nan")
    }
    fund_divs = fp.fetch_annual_dividends(funds) if funds else {}

    us_tickers = {
        str(r.get("ticker", "")).strip() for r in rows
        if str(r.get("market", "")).strip() == "us"
    }
    if us_tickers & fetched.keys():
        fx_rate = pr.fetch_fx_rate()
        if fx_rate is None:
            print("為替レートを取得できないため、米国銘柄の配当は記録しません。", file=sys.stderr)
        fetched = pr.convert_us_values_to_jpy(fetched, us_tickers, fx_rate)
    return {**div_map, **fund_divs, **fetched}


def read_cash_local(path: str) -> list[dict]:
    """ローカルの現金CSVを読む。無ければ空（現金未入力＝総資産＝運用資産）。"""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8-sig") as f:
        return ca.parse_csv(f.read())


def build(rows: list[dict], on: date, fetch: bool = True,
          cash_rows: list[dict] | None = None) -> dict:
    """保有行からスナップショット1行を作る（時価は price 列を使う）。

    時価に price 列を使うのは、時価更新（refresh_prices）の直後に走らせる前提のため。
    スナップショットが自分でも時価を引くと、2つの経路で別々の値が入りうる。
    """
    div_map = dividend_map_from_csv(rows)
    if fetch:
        div_map = fetch_missing_dividends(rows, div_map)
    holdings = pf.build_holdings(rows, {})
    return sn.build_record(holdings, div_map, on=on, cash_total=ca.total(cash_rows or []))


def _describe(record: dict) -> str:
    return (
        f"{record['date']}：評価額 {int(record['total_market']):,}円 / "
        f"元本 {int(record['total_cost']):,}円 / 含み損益 {int(record['gain']):,}円 / "
        f"年間配当（税抜）{int(record['annual_dividend_after_tax']):,}円 / "
        f"現金 {int(record['cash']):,}円 → 総資産 {int(record['net_worth']):,}円"
    )


def run_local(holdings_path: str, snapshots_path: str, on: date, dry_run: bool,
              fetch: bool = True, cash_path: str = CASH_CSV) -> int:
    if not os.path.exists(holdings_path):
        print(f"holdings.csv が見つかりません：{holdings_path}", file=sys.stderr)
        return 1
    with open(holdings_path, "r", encoding="utf-8-sig") as f:
        rows = dataio.parse_holdings_csv(f.read())
    if not rows:
        print(f"holdings.csv が空です：{holdings_path}", file=sys.stderr)
        return 1

    record = build(rows, on, fetch, read_cash_local(cash_path))
    print(_describe(record))
    if dry_run:
        print("--dry-run のため書き込みません。")
        return 0

    existing = []
    if os.path.exists(snapshots_path):
        with open(snapshots_path, "r", encoding="utf-8-sig") as f:
            existing = sn.parse_csv(f.read())
    text = sn.serialize_csv(sn.upsert(existing, record))
    with open(snapshots_path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    print(f"書込先：{snapshots_path}")
    return 0


def run_storage(on: date, dry_run: bool, fetch: bool = True) -> int:
    cfg = resolve_config()
    if cfg is None:
        print(
            "保存先が未設定です。環境変数 ASSET_STORAGE_TOKEN/_OWNER/_REPO を設定するか、\n"
            "400_Asset-management/.streamlit/secrets.toml に [storage] を書いてください。\n"
            "ローカルCSVだけ更新する場合は --local を付けてください。",
            file=sys.stderr,
        )
        return 1

    holdings_text, _ = sg.load(cfg)
    if not holdings_text:
        ok, message = sg.check(cfg)
        print(f"保存先から読み込めませんでした。{message}", file=sys.stderr)
        return 1

    cash_text, _ = sg.load(dataclasses.replace(cfg, path=CASH_PATH))
    record = build(dataio.parse_holdings_csv(holdings_text), on, fetch, ca.parse_csv(cash_text))
    print(_describe(record))
    if dry_run:
        print("--dry-run のため書き込みません。")
        return 0

    snap_cfg = dataclasses.replace(cfg, path=SNAPSHOT_PATH)
    existing_text, sha = sg.load(snap_cfg)
    text = sn.serialize_csv(sn.upsert(sn.parse_csv(existing_text), record))

    ok, message = sg.save(snap_cfg, text, sha, f"record snapshot ({record['date']})")
    print(message)
    if not ok:
        return 1
    print(f"保存先：{snap_cfg.owner}/{snap_cfg.repo}/{snap_cfg.path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="資産スナップショット（月1行）を記録する")
    parser.add_argument(
        "--local", action="store_true",
        help="保存先ではなくローカルCSVへ記録する（GitHub Actions・オフライン用）",
    )
    parser.add_argument("--file", default=HOLDINGS_CSV, help="--local 時の holdings.csv パス")
    parser.add_argument("--out", default=SNAPSHOTS_CSV, help="--local 時の snapshots.csv パス")
    parser.add_argument("--cash", default=CASH_CSV, help="--local 時の cash.csv パス")
    parser.add_argument("--dry-run", action="store_true", help="記録内容を表示するだけ")
    parser.add_argument(
        "--no-fetch", action="store_true",
        help="配当をyfinanceから補わない（CSVの div_per_share だけで記録する）",
    )
    args = parser.parse_args()

    today = date.today()
    fetch = not args.no_fetch
    if args.local:
        return run_local(args.file, args.out, today, args.dry_run, fetch, args.cash)
    return run_storage(today, args.dry_run, fetch)


if __name__ == "__main__":
    raise SystemExit(main())
