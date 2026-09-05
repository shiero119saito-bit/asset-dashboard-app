"""app.main() を通しで実行するスモークテスト。

純関数テストでは拾えない「UI組み立ての順序ミス」を検出するために置く。
実際に UnboundLocalError（holdings を定義前に参照）をクラウドで踏んだため、
ローカルで同じ経路を通す。Streamlit の各ウィジェットは最小限のスタブで置換し、
描画はせず例外だけを見る。
"""
import os
import sys
import types
from datetime import date

import pytest

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)


class _Stub:
    """Streamlit ウィジェットの汎用スタブ。呼び出しを受けて既定値を返す。"""

    def __init__(self, values=None):
        self._values = values or {}

    # レイアウト系：自分自身（または複数）を返して連鎖呼び出しを成立させる
    def columns(self, spec, **kw):
        n = spec if isinstance(spec, int) else len(spec)
        return [self for _ in range(n)]

    def tabs(self, labels):
        return [self for _ in labels]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    # 入力系：既定値を返す
    def radio(self, label, options, **kw):
        return options[0]

    def selectbox(self, label, options, **kw):
        return options[0]

    def multiselect(self, label, options, **kw):
        return kw.get("default", list(options))

    def number_input(self, label, value=0, **kw):
        return value

    def slider(self, label, min_value=0, max_value=100, value=0, **kw):
        return value

    def date_input(self, label, value=None, **kw):
        return value or date(1980, 1, 1)

    def checkbox(self, label, value=False, **kw):
        return value

    def button(self, label, **kw):
        return False  # 押されていない状態＝重い処理は走らせない

    def file_uploader(self, label, **kw):
        return None

    def data_editor(self, data, **kw):
        return data  # 編集なしでそのまま返す

    def download_button(self, label, **kw):
        return False

    # 出力系：何もしない
    def __getattr__(self, name):
        return lambda *a, **kw: None


class _ColumnConfig:
    """st.column_config.* のスタブ（設定オブジェクトは使われないので None でよい）。"""

    def __getattr__(self, name):
        return lambda *a, **kw: None


class _CacheData:
    """st.cache_data のスタブ。デコレータとしても `.clear()` としても使われる。

    メソッドで実装すると `st.cache_data.clear()` が bound method への属性参照になり
    AttributeError になるため、呼び出し可能なオブジェクトにする。
    """

    def __call__(self, *a, **kw):
        if a and callable(a[0]):  # @st.cache_data（引数なし）
            return a[0]
        return lambda fn: fn      # @st.cache_data(...)（引数つき）

    def clear(self):
        return None


class _Secrets(dict):
    """未設定のキーは KeyError＝ローカル相当。渡した分だけ設定済みとして振る舞う。"""

    def __getitem__(self, key):
        if key in self.keys():
            return dict.__getitem__(self, key)
        raise KeyError(key)


class _StreamlitStub(_Stub):
    """モジュールとして振る舞うスタブ（未定義の st.xxx は no-op を返す）。"""

    def __init__(self, use_live: bool, secrets: dict | None = None):
        super().__init__()
        self.sidebar = _Stub()
        self.secrets = _Secrets(secrets or {})
        self.column_config = _ColumnConfig()
        self.session_state: dict = {}  # 実物は dict ライク。get/pop がそのまま使える
        self.cache_data = _CacheData()
        self._use_live = use_live

    def set_page_config(self, **kw):
        return None

    def stop(self):
        raise AssertionError("st.stop が呼ばれた（データ読込に失敗している）")

    def checkbox(self, label, value=False, **kw):
        return self._use_live


def _install_streamlit_stub(monkeypatch, use_live: bool, secrets: dict | None = None):
    st = _StreamlitStub(use_live, secrets)
    st.sidebar.checkbox = st.checkbox
    monkeypatch.setitem(sys.modules, "streamlit", st)
    return st


# 保存先が設定済みの状態（クラウドの実状態）。通信は下でスタブに差し替える
STORAGE_SECRETS = {"storage": {"token": "t", "owner": "o", "repo": "r"}}


def _run_main(
    monkeypatch, use_live: bool, secrets: dict | None = None, press_buttons: bool = False
):
    st = _install_streamlit_stub(monkeypatch, use_live, secrets)
    if press_buttons:
        st.button = lambda label, **kw: True
        st.sidebar.button = st.button

    for mod in ("app", "portfolio", "dividend", "prices", "dataio", "simulation", "storage"):
        sys.modules.pop(mod, None)

    import prices as pr
    import storage as sg

    # 通信させない：ライブ取得は常に空＝保存時価/取得単価へフォールバックする経路
    monkeypatch.setattr(pr, "fetch_prices", lambda tickers: {})
    monkeypatch.setattr(pr, "fetch_dividends", lambda tickers: {})
    monkeypatch.setattr(pr, "fetch_dividend_months", lambda tickers: {})
    monkeypatch.setattr(pr, "fetch_fx_rate", lambda: None)
    # 書き込み系も必ず塞ぐ。テストが GitHub へ実際に PUT / dispatch すると事故になる
    monkeypatch.setattr(sg, "load", lambda cfg: (None, None))
    monkeypatch.setattr(sg, "save", lambda *a, **kw: (True, "保存しました。"))
    monkeypatch.setattr(sg, "check", lambda cfg: (True, "接続できました。"))
    monkeypatch.setattr(sg, "trigger_workflow", lambda *a, **kw: (True, "依頼しました。"))

    import app

    monkeypatch.setattr(app, "save_birth_date", lambda birth: True)  # 設定ファイルを汚さない
    app.main()  # 例外が出なければ成功


@pytest.mark.parametrize("use_live", [False, True])
def test_main_runs_without_error(monkeypatch, use_live):
    """時価取得の ON/OFF どちらでも main() が例外なく最後まで走ること。

    use_live=True でライブ取得が空になる経路（=クラウドで起きた状態）も通す。
    """
    _run_main(monkeypatch, use_live)


@pytest.mark.parametrize("use_live", [False, True])
def test_main_runs_with_storage_configured(monkeypatch, use_live):
    """保存先が設定済みの経路も通すこと。

    クラウドは常にこちら（保存ボタン・時価更新ボタンが出る側）で動く。
    未設定の経路だけ緑にして安心していると、画面にしか現れない不具合を素通しする
    ＝実際に UnboundLocalError をクラウドで踏んだのと同じ穴になる。
    """
    _run_main(monkeypatch, use_live, STORAGE_SECRETS)


def test_main_runs_when_buttons_are_pressed(monkeypatch):
    """ボタンを押した経路も例外なく走ること（保存・接続確認・時価更新・再読込）。

    押下時にしか通らない呼び出し（`st.cache_data.clear()` など）は、押されていない
    前提のテストでは永久に検証されない。外部への書き込みはすべてスタブで塞いである。
    """
    _run_main(monkeypatch, use_live=False, secrets=STORAGE_SECRETS, press_buttons=True)
