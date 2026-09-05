"""fundprices.py のテスト。requests はスタブに差し替え、実際の通信はしない。"""
import os
import sys
import types
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fundprices as fp  # noqa: E402

# 投資信託協会CSVの実物の形（Shift-JIS・1万口あたりの基準価額・日付昇順）
SAMPLE = (
    "年月日,基準価額(円),純資産総額（百万円）,分配金,決算期\n"
    "2026年09月02日,38584,13791718,,\n"
    "2026年09月03日,38285,13748168,,\n"
    "2026年09月04日,37945,13642433,,\n"
)


class _Response:
    def __init__(self, status_code: int, content: bytes = b""):
        self.status_code = status_code
        self.content = content


class _RequestsStub(types.ModuleType):
    def __init__(self, result=None):
        super().__init__("requests")
        self._result = result
        self.calls: list[tuple] = []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _install(monkeypatch, stub):
    monkeypatch.setitem(sys.modules, "requests", stub)
    return stub


# --- CSVの解釈（純関数）---


def test_parse_returns_latest_row_converted_to_per_unit():
    """基準価額は1万口あたり。holdings.csv の cost_per_share は1口あたりなので揃える。

    ここを混ぜると評価額が1万倍ずれる（口数がそのまま掛かるため）。
    """
    got = fp.parse_nav_csv(SAMPLE)
    assert got == (date(2026, 9, 4), 3.7945)


def test_parse_skips_trailing_blank_and_broken_rows():
    # 末尾の1行が壊れていても、その手前の有効な行を採る（全体を捨てない）
    text = SAMPLE + "\n,,,,\n2026年09月05日,,,,\n"
    assert fp.parse_nav_csv(text) == (date(2026, 9, 4), 3.7945)


def test_parse_returns_none_when_no_data_row():
    assert fp.parse_nav_csv("年月日,基準価額(円)\n") is None
    assert fp.parse_nav_csv("") is None


def test_parse_ignores_non_positive_nav():
    assert fp.parse_nav_csv("年月日,基準価額(円)\n2026年09月04日,0,,,\n") is None


def test_parse_accepts_thousands_separator():
    assert fp.parse_nav_csv('年月日,基準価額(円)\n2026年09月04日,"37,945",,,\n') == (
        date(2026, 9, 4), 3.7945
    )


# --- 取得（通信あり・スタブ）---


def test_fetch_nav_sends_both_codes(monkeypatch):
    """ISINと協会コードの2つでファンドが特定される。片方だけでは別ファンドを引きうる。"""
    stub = _install(monkeypatch, _RequestsStub(_Response(200, SAMPLE.encode("cp932"))))
    got = fp.fetch_nav("JP90C000H1T1", "0331418A")
    assert got == (date(2026, 9, 4), 3.7945)
    url, kw = stub.calls[0]
    assert kw["params"] == {"isinCd": "JP90C000H1T1", "associFundCd": "0331418A"}


def test_fetch_nav_decodes_shift_jis(monkeypatch):
    # UTF-8 として読むと日付列が壊れて全行スキップになる
    _install(monkeypatch, _RequestsStub(_Response(200, SAMPLE.encode("cp932"))))
    assert fp.fetch_nav("x", "y")[1] == 3.7945


def test_fetch_nav_returns_none_on_failure(monkeypatch):
    for result in (_Response(404), _Response(500), _Response(200, b""),
                   ConnectionError("offline")):
        _install(monkeypatch, _RequestsStub(result))
        assert fp.fetch_nav("x", "y") is None


def test_fetch_nav_requires_both_codes(monkeypatch):
    # 未設定の行（上場銘柄）で無駄な通信をしない
    stub = _install(monkeypatch, _RequestsStub(_Response(200, SAMPLE.encode("cp932"))))
    assert fp.fetch_nav("", "0331418A") is None
    assert fp.fetch_nav("JP90C000H1T1", "") is None
    assert stub.calls == []


def test_fetch_navs_omits_failures(monkeypatch):
    """取得できないファンドはキーを省略する（prices.fetch_prices と同じ約束）。

    呼び出し側は「キーが無ければ既存の値を残す」ため、0 を入れて評価額を消さない。
    """
    responses = {"ok": _Response(200, SAMPLE.encode("cp932")), "ng": _Response(404)}

    class _Multi(_RequestsStub):
        def get(self, url, **kw):
            return responses["ok" if kw["params"]["isinCd"] == "GOOD" else "ng"]

    _install(monkeypatch, _Multi())
    got = fp.fetch_navs({"良": ("GOOD", "1"), "駄": ("BAD", "2")})
    assert got == {"良": 3.7945}
