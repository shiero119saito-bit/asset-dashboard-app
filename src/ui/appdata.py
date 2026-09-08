"""保有データ・設定・付随データの読み書き。

保存先（private repo）→ ローカルファイル → 既定値、という多段フォールバックを
ここに集約する。storage.py（GitHub API）と dataio.py（純粋なパーサ）は触らず、
「どこから読んで、どこへ書くか」の判断だけを持つ。
"""
from __future__ import annotations

import dataclasses
import os
from datetime import date

import pandas as pd
import streamlit as st

import dataio
import storage as sg
import viewsettings as vs

from ui.constants import (
    DATA_DIR,
    GOALS_STATE,
    REAL_CSV,
    SAMPLE_CSV,
    SETTINGS_JSON,
    USER_SETTINGS_PATH,
    VIEW_SETTINGS_PATH,
)


def _read_local(path: str) -> str | None:
    """ローカルファイルを読む。無い・読めないなら None（保存先が未設定の環境向け）。"""
    try:
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    except OSError:
        return None


def _side_config(cfg: sg.StorageConfig | None, path: str) -> sg.StorageConfig | None:
    """保存先設定の path だけを差し替える（保有CSVと同じ repo の別ファイルを指す）。"""
    return dataclasses.replace(cfg, path=path) if cfg else None


def view_settings_config(cfg: sg.StorageConfig | None) -> sg.StorageConfig | None:
    """保存先設定の path だけを表示設定ファイルに差し替える。"""
    return dataclasses.replace(cfg, path=VIEW_SETTINGS_PATH) if cfg else None


def user_settings_config(cfg: sg.StorageConfig | None) -> sg.StorageConfig | None:
    """保存先設定の path だけを個人設定ファイルに差し替える。"""
    return dataclasses.replace(cfg, path=USER_SETTINGS_PATH) if cfg else None


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
        # 表示する中身はアップロードCSVだが、sha は保存先の現在値が要る。
        # GitHub は既存ファイルへの sha 無し PUT を 422 で弾くため、None のままだと
        # 「置換」で取り込んだ内容を保存できない（実害：HTTP 422）
        _, sha = sg.load(storage_config())
        return dataio.parse_holdings_csv(uploaded_text), "アップロードCSV", sha

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


def load_side_csv(state_key: str, path: str, local_path: str, parser):
    """付随データ（現金・スナップショット・配当実績）を読む。(rows, sha) を返す。

    **1セッションに1回だけ**読む。Streamlit は操作のたびに全再実行するため、
    毎回 GitHub API を叩くと操作が重くなる（時価取得で同じ問題を踏んでいる）。
    保存時は呼び出し側が session_state を更新する。
    """
    if state_key in st.session_state:
        return st.session_state[state_key]

    cfg = storage_config()
    text, sha = sg.load(_side_config(cfg, path))
    if text is None:
        text, sha = _read_local(local_path), None
    st.session_state[state_key] = (parser(text), sha)
    return st.session_state[state_key]


def save_side_csv(state_key: str, path: str, local_path: str, text: str, parser,
                  message: str) -> tuple[bool, str]:
    """付随データを保存する。保存先が無ければローカルへ書く（クラウドでは揮発）。"""
    cfg = storage_config()
    target = _side_config(cfg, path)
    if target is not None:
        _, sha = sg.load(target)
        ok, note = sg.save(target, text, sha, message)
        if ok:
            st.session_state.pop(state_key, None)
        return (ok, note)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(local_path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        st.session_state.pop(state_key, None)
        return (True, f"保存した（{os.path.basename(local_path)}）")
    except OSError:
        return (False, "保存できなかった（書き込み不可の環境）。")


def load_goals(cfg: sg.StorageConfig | None = None) -> dict[str, float]:
    """目標・前提値を読む。保存先 → ローカル → 既定値の順。

    **必ず既定値の上にマージして返す**。設定は後から項目が増えるため、古い保存内容や
    セッションに残った古い形の辞書をそのまま返すと、増えたキーの参照で KeyError になる
    （実際に `target_age` 追加時にクラウドで落ちた）。
    """
    if GOALS_STATE in st.session_state:
        return {**dataio.DEFAULT_GOALS, **st.session_state[GOALS_STATE]}
    text, _ = sg.load(user_settings_config(cfg))
    if text is None:
        text = _read_local(SETTINGS_JSON)
    st.session_state[GOALS_STATE] = dataio.parse_goals(text)
    return st.session_state[GOALS_STATE]


def save_goals(goals: dict[str, float], cfg: sg.StorageConfig | None = None) -> tuple[bool, str]:
    """目標額を保存する（生年月日と同じ設定JSONへマージする）。"""
    if cfg is not None:
        target = user_settings_config(cfg)
        existing, sha = sg.load(target)
        ok, message = sg.save(
            target, dataio.serialize_goals(goals, existing), sha, "update goals"
        )
        if ok:
            st.session_state[GOALS_STATE] = dict(goals)
        return (ok, "保存した" if ok else message)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        existing = _read_local(SETTINGS_JSON)
        with open(SETTINGS_JSON, "w", encoding="utf-8") as f:
            f.write(dataio.serialize_goals(goals, existing))
        st.session_state[GOALS_STATE] = dict(goals)
        return (True, "保存した")
    except OSError:
        return (False, "保存できなかった（書き込み不可の環境）。")


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
        existing, sha = sg.load(target)
        ok, message = sg.save(
            target, dataio.serialize_birth_date(birth, existing), sha, "update birth date"
        )
        return (ok, f"保存した（{birth}）" if ok else message)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        existing = _read_local(SETTINGS_JSON)
        with open(SETTINGS_JSON, "w", encoding="utf-8") as f:
            f.write(dataio.serialize_birth_date(birth, existing))
        return (True, f"保存した（{birth}）")
    except OSError:
        return (False, "保存できなかった（書き込み不可の環境）。今回のみ有効。")


def load_view_orders(cfg: sg.StorageConfig | None) -> dict[str, list[str]]:
    """保存された列順を読む。未設定・未作成なら空 dict（＝既定の並び）。"""
    text, _ = sg.load(view_settings_config(cfg))
    return vs.parse_orders(text)


def save_view_orders(cfg: sg.StorageConfig | None,
                     orders: dict[str, list[str]]) -> tuple[bool, str]:
    """列順を保存先へ書き戻す。sha を取り直してから書く（他端末の更新を弾く）。"""
    target = view_settings_config(cfg)
    _, sha = sg.load(target)
    return sg.save(target, vs.serialize_orders(orders), sha, "update view settings")
