"""storage.py のテスト。requests はスタブに差し替え、実際の通信はしない。"""
import base64
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage as sg  # noqa: E402


CFG = sg.StorageConfig(token="t0ken", owner="me", repo="data", path="holdings.csv")


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _RequestsStub(types.ModuleType):
    """requests の最小スタブ。呼び出し内容を記録する。"""

    def __init__(self, get_result=None, put_result=None):
        super().__init__("requests")
        self._get_result = get_result
        self._put_result = put_result
        self.calls: list[tuple] = []

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        if isinstance(self._get_result, Exception):
            raise self._get_result
        return self._get_result

    def put(self, url, **kw):
        self.calls.append(("PUT", url, kw))
        if isinstance(self._put_result, Exception):
            raise self._put_result
        return self._put_result


def _install(monkeypatch, stub):
    monkeypatch.setitem(sys.modules, "requests", stub)
    return stub


# --- 設定の組み立て ---


def test_build_config_from_secrets():
    cfg = sg.build_config({"token": "t", "owner": "o", "repo": "r"})
    assert cfg is not None
    assert (cfg.owner, cfg.repo, cfg.path, cfg.branch) == ("o", "r", "holdings.csv", "main")


def test_build_config_returns_none_when_incomplete():
    # 未設定・必須欠け・空文字はすべて「保存先なし」として扱う（例外にしない）
    assert sg.build_config(None) is None
    assert sg.build_config({}) is None
    assert sg.build_config({"token": "t", "owner": "o"}) is None
    assert sg.build_config({"token": "", "owner": "o", "repo": "r"}) is None


def test_config_repr_hides_token():
    # ログや例外にトークンが載らないこと
    assert "t0ken" not in repr(CFG)
    assert "owner='me'" in repr(CFG)


# --- 読み込み ---


def test_load_decodes_content_and_returns_sha(monkeypatch):
    body = "ticker,name\n1605,INPEX\n"
    encoded = base64.b64encode(body.encode("utf-8")).decode("ascii")
    stub = _install(monkeypatch, _RequestsStub(
        get_result=_Response(200, {"content": encoded, "sha": "abc123"})
    ))
    text, sha = sg.load(CFG)
    assert text == body
    assert sha == "abc123"
    method, url, kw = stub.calls[0]
    assert method == "GET"
    assert url.endswith("/repos/me/data/contents/holdings.csv")
    assert kw["headers"]["Authorization"] == "Bearer t0ken"


def test_load_returns_none_when_missing_or_unauthorized(monkeypatch):
    for status in (404, 401, 403, 500):
        _install(monkeypatch, _RequestsStub(get_result=_Response(status)))
        assert sg.load(CFG) == (None, None), f"status={status}"


def test_load_without_config_or_network_is_safe(monkeypatch):
    assert sg.load(None) == (None, None)
    _install(monkeypatch, _RequestsStub(get_result=ConnectionError("offline")))
    assert sg.load(CFG) == (None, None)


# --- 保存 ---


def test_save_sends_sha_and_encoded_content(monkeypatch):
    stub = _install(monkeypatch, _RequestsStub(put_result=_Response(200)))
    ok, msg = sg.save(CFG, "ticker,name\n1605,INPEX\n", "abc123", "update")
    assert ok is True
    _, _, kw = stub.calls[0]
    body = kw["json"]
    assert body["sha"] == "abc123"  # 競合検知のため必須
    assert base64.b64decode(body["content"]).decode("utf-8").startswith("ticker,name")
    assert body["message"] == "update"


def test_save_without_sha_creates_new_file(monkeypatch):
    stub = _install(monkeypatch, _RequestsStub(put_result=_Response(201)))
    ok, _ = sg.save(CFG, "x", None, "create")
    assert ok is True
    assert "sha" not in stub.calls[0][2]["json"]  # 新規作成時は sha を送らない


def test_save_conflict_tells_user_to_reload(monkeypatch):
    # 他端末が更新済み＝黙って上書きせず、再読込を促す
    _install(monkeypatch, _RequestsStub(put_result=_Response(409)))
    ok, msg = sg.save(CFG, "x", "old-sha", "update")
    assert ok is False
    assert "他の端末" in msg


@pytest.mark.parametrize("status,keyword", [(401, "権限"), (403, "権限"), (404, "見つかりません")])
def test_save_error_messages_are_actionable(monkeypatch, status, keyword):
    _install(monkeypatch, _RequestsStub(put_result=_Response(status)))
    ok, msg = sg.save(CFG, "x", None, "m")
    assert ok is False and keyword in msg


def test_save_without_config_or_network_is_safe(monkeypatch):
    ok, msg = sg.save(None, "x", None, "m")
    assert ok is False and "設定" in msg
    _install(monkeypatch, _RequestsStub(put_result=ConnectionError("offline")))
    ok, msg = sg.save(CFG, "x", None, "m")
    assert ok is False and "接続" in msg


def test_save_never_leaks_token_in_message(monkeypatch):
    for status in (401, 404, 500):
        _install(monkeypatch, _RequestsStub(put_result=_Response(status)))
        _, msg = sg.save(CFG, "x", None, "m")
        assert "t0ken" not in msg


# --- 設定値の正規化・接続確認 ---


def test_build_config_strips_whitespace_and_newlines():
    """貼り付け事故（改行混入）で HTTP ヘッダが壊れないこと。

    改行入りのトークンをそのままヘッダに入れると requests が InvalidHeader を投げ、
    「接続できない」ようにしか見えない失敗になる（実際に踏んだ）。
    """
    cfg = sg.build_config({
        "token": "github_pat_\nabc  def\r\n", "owner": " me \n", "repo": "\tdata ",
    })
    assert cfg.token == "github_pat_abcdef"
    assert cfg.owner == "me" and cfg.repo == "data"


def test_save_error_message_names_exception_type(monkeypatch):
    # 原因の切り分けができるよう例外の型を添える（トークンは載せない）
    _install(monkeypatch, _RequestsStub(put_result=ConnectionError("boom")))
    ok, msg = sg.save(CFG, "x", None, "m")
    assert ok is False
    assert "ConnectionError" in msg and "t0ken" not in msg


def test_check_distinguishes_states(monkeypatch):
    _install(monkeypatch, _RequestsStub(get_result=_Response(200, {"content": "", "sha": "s"})))
    assert sg.check(CFG)[0] is True

    # ファイル未作成（初回）は「到達できている」扱い
    _install(monkeypatch, _RequestsStub(get_result=_Response(404)))
    ok, msg = sg.check(CFG)
    assert ok is True and "未作成" in msg

    # 認証エラーは失敗＝何を直すべきか書く
    _install(monkeypatch, _RequestsStub(get_result=_Response(401)))
    ok, msg = sg.check(CFG)
    assert ok is False and "トークン" in msg

    assert sg.check(None)[0] is False
