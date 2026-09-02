"""保有データの永続化（外部依存=GitHub API を隔離）。

Streamlit Cloud はコンテナが揮発するため、画面で編集した内容をローカルファイルに
書いても次回起動で消える。そこで実データ専用の private リポジトリを保存先にし、
どの端末からでも同じ最新データを読めるようにする。

prices.py と同じ方針で、通信・認証はすべてこのモジュールに閉じ込める。
失敗時は例外を投げず None / (False, 理由) を返し、呼び出し側は
「保存できなかった」ことだけを扱えばよいようにする。

トークンは st.secrets からのみ受け取る。ログにも例外メッセージにも出さない。
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

API_ROOT = "https://api.github.com"
TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class StorageConfig:
    """保存先の指定。token は秘匿情報のため repr に出さない。"""

    token: str
    owner: str
    repo: str
    path: str = "holdings.csv"
    branch: str = "main"

    def __repr__(self) -> str:  # トークンの誤ログ出力を防ぐ
        return f"StorageConfig(owner={self.owner!r}, repo={self.repo!r}, path={self.path!r})"

    @property
    def contents_url(self) -> str:
        return f"{API_ROOT}/repos/{self.owner}/{self.repo}/contents/{self.path}"


def build_config(raw: dict | None) -> StorageConfig | None:
    """secrets の dict から設定を組み立てる。必須項目が欠ければ None（＝未設定）。"""
    if not raw:
        return None
    try:
        token = str(raw["token"]).strip()
        owner = str(raw["owner"]).strip()
        repo = str(raw["repo"]).strip()
    except (KeyError, TypeError):
        return None
    if not (token and owner and repo):
        return None
    return StorageConfig(
        token=token,
        owner=owner,
        repo=repo,
        path=str(raw.get("path") or "holdings.csv").strip(),
        branch=str(raw.get("branch") or "main").strip(),
    )


def _headers(cfg: StorageConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cfg.token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def load(cfg: StorageConfig | None) -> tuple[str | None, str | None]:
    """保存先から CSV 本文と sha を返す。取得できなければ (None, None)。

    sha は次回保存時の競合検知に使う（他端末が更新していたら PUT が弾かれる）。
    ファイルがまだ無い場合も (None, None)＝初回保存で新規作成される。
    """
    if cfg is None:
        return (None, None)
    try:
        import requests
    except ImportError:
        return (None, None)
    try:
        res = requests.get(
            cfg.contents_url,
            headers=_headers(cfg),
            params={"ref": cfg.branch},
            timeout=TIMEOUT_SECONDS,
        )
        if res.status_code != 200:
            return (None, None)
        payload = res.json()
        content = base64.b64decode(payload["content"]).decode("utf-8")
        return (content, payload.get("sha"))
    except Exception:
        # 通信不可・認証エラー・想定外のレスポンス形。呼び出し側は他ソースへ落ちる
        return (None, None)


def save(
    cfg: StorageConfig | None, text: str, sha: str | None, message: str
) -> tuple[bool, str]:
    """CSV を保存先へコミットする。(成功したか, 利用者向けメッセージ) を返す。

    sha を必ず添えるため、読み込んだ後に他端末が更新していれば GitHub 側が 409 を返す。
    黙って上書きせず、再読込を促すメッセージにする（データを失わせない）。
    """
    if cfg is None:
        return (False, "保存先が設定されていません。")
    try:
        import requests
    except ImportError:
        return (False, "requests が導入されていません。")

    body: dict = {
        "message": message,
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "branch": cfg.branch,
    }
    if sha:
        body["sha"] = sha  # 既存ファイルの更新。無い場合は新規作成

    try:
        res = requests.put(
            cfg.contents_url, headers=_headers(cfg), json=body, timeout=TIMEOUT_SECONDS
        )
    except Exception:
        return (False, "保存先に接続できませんでした。")

    if res.status_code in (200, 201):
        return (True, "保存しました。")
    if res.status_code == 409:
        return (False, "他の端末で更新されています。読み込み直してから保存してください。")
    if res.status_code in (401, 403):
        return (False, "保存先への権限がありません（トークンを確認してください）。")
    if res.status_code == 404:
        return (False, "保存先が見つかりません（owner/repo/path を確認してください）。")
    return (False, f"保存に失敗しました（HTTP {res.status_code}）。")
