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


class _Secrets(dict):
    def __getitem__(self, key):
        raise KeyError(key)  # secrets 未設定＝ローカル相当


class _StreamlitStub(_Stub):
    """モジュールとして振る舞うスタブ（未定義の st.xxx は no-op を返す）。"""

    def __init__(self, use_live: bool):
        super().__init__()
        self.sidebar = _Stub()
        self.secrets = _Secrets()
        self.column_config = _ColumnConfig()
        self._use_live = use_live

    def set_page_config(self, **kw):
        return None

    def cache_data(self, *a, **kw):
        # @st.cache_data / @st.cache_data(...) の両形式を素通しにする
        if a and callable(a[0]):
            return a[0]
        return lambda fn: fn

    def stop(self):
        raise AssertionError("st.stop が呼ばれた（データ読込に失敗している）")

    def checkbox(self, label, value=False, **kw):
        return self._use_live


def _install_streamlit_stub(monkeypatch, use_live: bool):
    st = _StreamlitStub(use_live)
    st.sidebar.checkbox = st.checkbox
    monkeypatch.setitem(sys.modules, "streamlit", st)
    return st


@pytest.mark.parametrize("use_live", [False, True])
def test_main_runs_without_error(monkeypatch, use_live):
    """時価取得の ON/OFF どちらでも main() が例外なく最後まで走ること。

    use_live=True でライブ取得が空になる経路（=クラウドで起きた状態）も通す。
    """
    _install_streamlit_stub(monkeypatch, use_live)
    for mod in ("app", "portfolio", "dividend", "prices", "dataio", "simulation"):
        sys.modules.pop(mod, None)

    import prices as pr

    # 通信させない：ライブ取得は常に空＝保存時価/取得単価へフォールバックする経路
    monkeypatch.setattr(pr, "fetch_prices", lambda tickers: {})
    monkeypatch.setattr(pr, "fetch_dividends", lambda tickers: {})
    monkeypatch.setattr(pr, "fetch_dividend_months", lambda tickers: {})
    monkeypatch.setattr(pr, "fetch_fx_rate", lambda: None)

    import app

    app.main()  # 例外が出なければ成功
