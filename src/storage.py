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


def _clean(value) -> str:
    """設定値から空白・改行を除去する。

    Secrets へ貼り付ける際にトークンが折り返されて改行が混ざることがある。
    そのまま HTTP ヘッダに入れると requests が InvalidHeader を投げ、
    「接続できない」ようにしか見えない失敗になるため、ここで落とす。
    """
    return "".join(str(value).split())


def build_config(raw: dict | None) -> StorageConfig | None:
    """secrets の dict から設定を組み立てる。必須項目が欠ければ None（＝未設定）。"""
    if not raw:
        return None
    try:
        token = _clean(raw["token"])
        owner = _clean(raw["owner"])
        repo = _clean(raw["repo"])
    except (KeyError, TypeError):
        return None
    if not (token and owner and repo):
        return None
    return StorageConfig(
        token=token,
        owner=owner,
        repo=repo,
        path=_clean(raw.get("path") or "holdings.csv"),
        branch=_clean(raw.get("branch") or "main"),
    )


def _headers(cfg: StorageConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cfg.token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def check(cfg: StorageConfig | None) -> tuple[bool, str]:
    """保存先へ到達できるか確かめる（読み取りのみ）。設定直後の切り分け用。

    ファイルが未作成（404）でも「repo には届いている」ことが分かるよう区別して返す。
    """
    if cfg is None:
        return (False, "保存先が設定されていません。")
    try:
        import requests
    except ImportError:
        return (False, "requests が導入されていません。")
    try:
        res = requests.get(
            cfg.contents_url, headers=_headers(cfg),
            params={"ref": cfg.branch}, timeout=TIMEOUT_SECONDS,
        )
    except Exception as e:
        return (False, f"接続できません（{type(e).__name__}）。")

    if res.status_code == 200:
        return (True, "保存先に接続でき、ファイルもあります。")
    if res.status_code == 404:
        # repo が無い場合も 404 なので、両方を疑えるよう書く
        return (True, "保存先に接続できました（ファイルは未作成。保存すると作られます）。"
                      "※ repo 名が違う場合も同じ表示になります。")
    if res.status_code in (401, 403):
        return (False, "認証に失敗しました。トークンの権限（Contents: Read and write）と"
                       "対象リポジトリの指定を確認してください。")
    return (False, f"接続に失敗しました（HTTP {res.status_code}）。")


def trigger_workflow(
    cfg: StorageConfig | None, workflow_file: str = "refresh-prices.yml"
) -> tuple[bool, str]:
    """保存先 repo の GitHub Actions を起動する（workflow_dispatch）。

    アプリ自身は Yahoo Finance から時価を取得できない（Streamlit Cloud の IP が 401 で
    弾かれる）ため、取得できる場所＝Actions に肩代わりさせる。画面のボタンから呼ぶ。

    起動は非同期で、成功は「受け付けられた」ことしか意味しない（204 No Content）。
    完了は数十秒後になるため、呼び出し側は再読込を促すこと。

    Contents しか許可していないトークンでは 403 になる。これは設定漏れであって
    障害ではないので、何を足せばよいかを本文に書いて返す。
    """
    if cfg is None:
        return (False, "保存先が設定されていません。")
    try:
        import requests
    except ImportError:
        return (False, "requests が導入されていません。")

    url = (
        f"{API_ROOT}/repos/{cfg.owner}/{cfg.repo}/actions/workflows/"
        f"{workflow_file}/dispatches"
    )
    try:
        res = requests.post(
            url, headers=_headers(cfg), json={"ref": cfg.branch}, timeout=TIMEOUT_SECONDS
        )
    except Exception as e:
        return (False, f"更新を依頼できませんでした（{type(e).__name__}）。")

    if res.status_code == 204:
        return (True, "時価の更新を依頼しました（1分ほどで反映されます）。")
    if res.status_code in (401, 403):
        return (False, "トークンに Actions の権限がありません"
                       "（Permissions に Actions: Read and write を追加してください）。")
    if res.status_code == 404:
        return (False, f"更新の仕組みが見つかりません（{workflow_file} が保存先 repo に"
                       "置かれていないか、まだ既定ブランチに入っていません）。")
    return (False, f"更新を依頼できませんでした（HTTP {res.status_code}）。")


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
    except Exception as e:
        # 例外の型だけ添える。トークンは Authorization ヘッダにあり例外文には出ないが、
        # 念のため型名以外は載せない。原因の切り分けにはこれで足りる
        # （InvalidHeader＝トークンに不正文字／ConnectionError＝到達不可 等）。
        return (False, f"保存先に接続できませんでした（{type(e).__name__}）。")

    if res.status_code in (200, 201):
        return (True, "保存しました。")
    if res.status_code == 409:
        return (False, "他の端末で更新されています。読み込み直してから保存してください。")
    if res.status_code == 422:
        # 既存ファイルなのに sha を添えていない（保存先の現在値が取れていない）場合に出る。
        # HTTP番号だけでは何をすればよいか分からないので、次の操作を書く
        return (False, "保存先の最新状態を取得できていません。"
                       "ページを再読み込みしてから、もう一度保存してください。")
    if res.status_code in (401, 403):
        return (False, "保存先への権限がありません（トークンを確認してください）。")
    if res.status_code == 404:
        return (False, "保存先が見つかりません（owner/repo/path を確認してください）。")
    return (False, f"保存に失敗しました（HTTP {res.status_code}）。")
