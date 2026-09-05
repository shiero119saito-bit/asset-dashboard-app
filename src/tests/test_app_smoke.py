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

    def __init__(self, values=None, button_log=None):
        self._values = values or {}
        # 描画されたボタンのラベル。「出るはずのボタンが出ない」を検出するために記録する
        self.button_log = button_log if button_log is not None else []

    # レイアウト系：自分自身（または複数）を返して連鎖呼び出しを成立させる
    def columns(self, spec, **kw):
        n = spec if isinstance(spec, int) else len(spec)
        return [self for _ in range(n)]

    def tabs(self, labels):
        return [self for _ in labels]

    def expander(self, label, **kw):
        return self  # with 構文で使うため self を返す（no-op の lambda では入れない）

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
        self.button_log.append(label)
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
        self.sidebar = _Stub(button_log=self.button_log)  # ログを共有する
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

    # 設定ファイルを汚さない／GitHub へ書かない
    monkeypatch.setattr(app, "save_birth_date", lambda birth, cfg=None: (True, "保存した"))
    app.main()  # 例外が出なければ成功
    return st


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


def test_purpose_options_include_growth(monkeypatch):
    """用途の選択肢に「資産形成」があること。

    当初は日本個別株の高配当/優待を分ける列だったが、インデックス（オルカン等）は
    資産最大化が目的で配当も優待も当てはまらず、未分類のままになっていた。
    """
    _install_streamlit_stub(monkeypatch, use_live=False)
    for mod in ("app",):
        sys.modules.pop(mod, None)
    import app

    assert app.PURPOSE_LABELS_BY_VALUE["growth"] == "資産形成"
    assert app.PURPOSE_LABELS[""] == "未分類"  # 取込直後の既定値は未分類のまま
    assert set(app.PURPOSE_LABELS_BY_VALUE) == {"dividend", "growth", "yutai"}


def _app_with_storage_stub(monkeypatch, saved: dict):
    """storage を書き込み記録用スタブに差し替えた app を返す。"""
    _install_streamlit_stub(monkeypatch, use_live=False)
    for mod in ("app", "storage"):
        sys.modules.pop(mod, None)
    import storage as sg

    monkeypatch.setattr(sg, "load", lambda cfg: (None, None))
    monkeypatch.setattr(
        sg, "save",
        lambda cfg, text, sha, msg: (saved.update(path=cfg.path, text=text), (True, "ok"))[1],
    )
    import app
    return app, sg


def test_settings_are_written_to_their_own_files_not_holdings(monkeypatch):
    """設定の保存が holdings.csv を上書きしないこと。

    保存先の指定は path だけを差し替える実装なので、差し替えを忘れると**資産データを
    設定ファイルで丸ごと潰す**。取り返しがつかないため、パスを固定して守る。
    """
    saved: dict = {}
    app, sg = _app_with_storage_stub(monkeypatch, saved)
    cfg = sg.StorageConfig(token="t", owner="o", repo="r", path="holdings.csv")

    ok, _ = app.save_birth_date(date(1983, 8, 21), cfg)
    assert ok and saved["path"] == "user_settings.json"
    assert "1983-08-21" in saved["text"]

    saved.clear()
    ok, _ = app.save_view_orders(cfg, {"cols_holdings": ["銘柄"]})
    assert ok and saved["path"] == "view_settings.json"
    assert "cols_holdings" in saved["text"]


def test_birth_date_falls_back_to_local_file_without_storage(monkeypatch, tmp_path):
    # 保存先が未設定なら従来どおりローカルへ書く（ローカル起動を壊さない）
    saved: dict = {}
    app, _ = _app_with_storage_stub(monkeypatch, saved)
    monkeypatch.setattr(app, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app, "SETTINGS_JSON", str(tmp_path / "user_settings.json"))

    ok, _ = app.save_birth_date(date(1983, 8, 21), None)
    assert ok and saved == {}  # storage へは書かない
    assert app.load_birth_date(None) == date(1983, 8, 21)


def test_save_buttons_appear_when_storage_is_configured(monkeypatch):
    """保存先が設定済みなら、各表に「この並びを保存」が出ること。

    show_table への cfg 引き渡しを落とすとボタンが静かに消える（`cfg is not None and
    st.button(...)` の短絡評価で描画自体が起きない）。実際にそれを踏み、画面を見るまで
    気付けなかった。ラベルが描画されたかで守る。
    """
    st = _run_main(monkeypatch, use_live=False, secrets=STORAGE_SECRETS)
    assert "この並びを保存" in st.button_log
    assert "時価を今すぐ更新" in st.button_log  # 同じ理由で消えうる導線
    assert "生年月日を保存" in st.button_log


def test_no_save_button_without_storage(monkeypatch):
    # 保存先が無いときは出さない（押しても保存できないボタンを見せない）
    st = _run_main(monkeypatch, use_live=False)
    assert "この並びを保存" not in st.button_log


def test_main_runs_with_every_allocation_axis(monkeypatch):
    """集計軸をどれに切り替えても main() が通ること（口座区分の軸を含む）。

    既定のスタブは options[0]（＝資産クラス）しか返さないため、後から足した軸は
    一度も実行されないまま緑になる。軸ごとに描画の分岐が違うので通しておく。
    """
    for index in (0, 1, 2, 3):
        st = _install_streamlit_stub(monkeypatch, use_live=False, secrets=STORAGE_SECRETS)
        base_radio = st.radio
        st.radio = (
            lambda label, options, _i=index, **kw:
            options[min(_i, len(options) - 1)] if label == "集計軸" else base_radio(label, options, **kw)
        )
        for mod in ("app", "portfolio", "dividend", "prices", "dataio", "simulation", "storage"):
            sys.modules.pop(mod, None)
        import prices as pr
        import storage as sg
        monkeypatch.setattr(pr, "fetch_prices", lambda tickers: {})
        monkeypatch.setattr(pr, "fetch_dividends", lambda tickers: {})
        monkeypatch.setattr(pr, "fetch_dividend_months", lambda tickers: {})
        monkeypatch.setattr(pr, "fetch_fx_rate", lambda: None)
        monkeypatch.setattr(sg, "load", lambda cfg: (None, None))
        import app
        monkeypatch.setattr(app, "save_birth_date", lambda birth, cfg=None: (True, "保存した"))
        app.main()


def test_account_labels_cover_all_stored_values(monkeypatch):
    """画面の表示名が保存されうる値をすべて網羅していること。

    欠けると内部値（nisa_growth 等）がそのまま画面に出る。
    """
    _install_streamlit_stub(monkeypatch, use_live=False)
    for mod in ("app", "dataio"):
        sys.modules.pop(mod, None)
    import dataio
    import app

    for value in dataio.ACCOUNTS:
        assert value in app.ACCOUNT_LABELS
    assert app.ACCOUNT_LABELS[""] == "特定"  # 空欄は特定扱い（税計算と揃える）
    assert app.ACCOUNT_LABELS["nisa_growth"] == "成長投資枠"
