"""app.main() を通しで実行するスモークテスト。

純関数テストでは拾えない「UI組み立ての順序ミス」を検出するために置く。
実際に UnboundLocalError（holdings を定義前に参照）をクラウドで踏んだため、
ローカルで同じ経路を通す。Streamlit の各ウィジェットは最小限のスタブで置換し、
描画はせず例外だけを見る。
"""
import ast
import os
import sys
import types
from datetime import date

import pytest

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)


# app / ui.* / 業務モジュールを import し直すための一括破棄。
# ui の各モジュールは import 時に `import streamlit as st` を束縛するため、
# 落とさないと**前のテストのスタブを掴んだまま**になる（分割前は app だけで足りた）。
_SRC_MODULES = (
    "app", "portfolio", "dividend", "prices", "dataio", "simulation", "storage",
    "snapshots", "cash", "dividend_history", "income", "fundprices",
    "pricing_update", "viewsettings",
)


def _reset_modules() -> None:
    for name in _SRC_MODULES:
        sys.modules.pop(name, None)
    for name in [k for k in sys.modules if k == "ui" or k.startswith("ui.")]:
        sys.modules.pop(name, None)


class _Stub:
    """Streamlit ウィジェットの汎用スタブ。呼び出しを受けて既定値を返す。"""

    def __init__(self, values=None, button_log=None, tab_log=None):
        self._values = values or {}
        # 描画されたボタンのラベル。「出るはずのボタンが出ない」を検出するために記録する
        self.button_log = button_log if button_log is not None else []
        self.tab_log = tab_log if tab_log is not None else []

    # レイアウト系：自分自身（または複数）を返して連鎖呼び出しを成立させる
    def columns(self, spec, **kw):
        n = spec if isinstance(spec, int) else len(spec)
        return [self for _ in range(n)]

    def tabs(self, labels):
        # どのタブが作られたかを検証できるよう記録する（構成の取りこぼし検出）
        self.tab_log.append(list(labels))
        return [self for _ in labels]

    def expander(self, label, **kw):
        return self  # with 構文で使うため self を返す（no-op の lambda では入れない）

    def container(self, **kw):
        return self  # KPIカード（border=True）も with 構文で使う

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

    def __init__(self, checkbox_value: bool, secrets: dict | None = None):
        super().__init__()
        self.sidebar = _Stub(button_log=self.button_log, tab_log=self.tab_log)  # ログを共有する
        self.secrets = _Secrets(secrets or {})
        self.column_config = _ColumnConfig()
        self.session_state: dict = {}  # 実物は dict ライク。get/pop がそのまま使える
        self.cache_data = _CacheData()
        self._checkbox_value = checkbox_value

    def set_page_config(self, **kw):
        return None

    def stop(self):
        raise AssertionError("st.stop が呼ばれた（データ読込に失敗している）")

    def fragment(self, func=None, **kw):
        """@st.fragment のスタブ。関数をそのまま返す（テストでは分割実行しない）。

        `__getattr__` の no-op が返るとデコレータが関数を None に潰し、
        import 時点で全モジュールが壊れる（_CacheData と同じ理由でここに置く）。
        """
        return func if func is not None else (lambda fn: fn)

    def checkbox(self, label, value=False, **kw):
        return self._checkbox_value


def _install_streamlit_stub(monkeypatch, checkbox_value: bool, secrets: dict | None = None):
    st = _StreamlitStub(checkbox_value, secrets)
    st.sidebar.checkbox = st.checkbox
    monkeypatch.setitem(sys.modules, "streamlit", st)
    return st


# 保存先が設定済みの状態（クラウドの実状態）。通信は下でスタブに差し替える
STORAGE_SECRETS = {"storage": {"token": "t", "owner": "o", "repo": "r"}}


def _run_main(
    monkeypatch, checkbox_value: bool, secrets: dict | None = None,
    press_buttons: bool = False,
):
    st = _install_streamlit_stub(monkeypatch, checkbox_value, secrets)
    if press_buttons:
        st.button = lambda label, **kw: True
        st.sidebar.button = st.button

    _reset_modules()

    import prices as pr
    import storage as sg

    # 通信させない。画面はもう yfinance を呼ばない（保存値を読むだけ）が、
    # 経路が戻ってきたときに本当に通信してしまわないよう塞いだままにする
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
    from ui import datasource as ui_datasource

    # 設定ファイルを汚さない／GitHub へ書かない
    monkeypatch.setattr(ui_datasource, "save_birth_date", lambda birth, cfg=None: (True, "保存した"))
    app.main()  # 例外が出なければ成功
    return st


@pytest.mark.parametrize("checkbox_value", [False, True])
def test_main_runs_without_error(monkeypatch, checkbox_value):
    """チェックボックスの ON/OFF どちらでも main() が例外なく最後まで走ること。

    画面のチェックボックスは業種軸の「日本個別株のみ」だけになったので、
    両方を通すと集計軸の両分岐（個別株のみ／ETF・投信を含む）を踏む。
    """
    _run_main(monkeypatch, checkbox_value)


@pytest.mark.parametrize("checkbox_value", [False, True])
def test_main_runs_with_storage_configured(monkeypatch, checkbox_value):
    """保存先が設定済みの経路も通すこと。

    クラウドは常にこちら（保存ボタン・時価更新ボタンが出る側）で動く。
    未設定の経路だけ緑にして安心していると、画面にしか現れない不具合を素通しする
    ＝実際に UnboundLocalError をクラウドで踏んだのと同じ穴になる。
    """
    _run_main(monkeypatch, checkbox_value, STORAGE_SECRETS)


def test_main_runs_when_buttons_are_pressed(monkeypatch):
    """ボタンを押した経路も例外なく走ること（保存・接続確認・時価更新・再読込）。

    押下時にしか通らない呼び出し（`st.cache_data.clear()` など）は、押されていない
    前提のテストでは永久に検証されない。外部への書き込みはすべてスタブで塞いである。
    """
    _run_main(monkeypatch, checkbox_value=False, secrets=STORAGE_SECRETS, press_buttons=True)


def test_main_does_not_fetch_from_yfinance(monkeypatch):
    """初回表示で yfinance を1度も呼ばないこと。

    以前は画面を開くたびに 87銘柄 × 3系統（時価・配当・権利月）＋為替を取りに行き、
    Streamlit Cloud では Yahoo が 401 を返すため**待たされた末に取得できない**状態だった。
    取得は refresh_prices.py（PC・Actions）に寄せ、画面は holdings.csv の保存値を読む。
    経路が戻ると同じ待ち時間が復活するので、呼ばれたら落ちるスタブで固定する。
    """
    st = _install_streamlit_stub(monkeypatch, checkbox_value=False, secrets=STORAGE_SECRETS)
    _reset_modules()

    import prices as pr
    import storage as sg

    def _forbidden(*a, **kw):
        raise AssertionError("画面から yfinance を呼んでいる")

    for name in ("fetch_prices", "fetch_dividends", "fetch_dividend_months",
                 "fetch_fx_rate", "fetch_price_history"):
        monkeypatch.setattr(pr, name, _forbidden)
    monkeypatch.setattr(sg, "load", lambda cfg: (None, None))

    import app
    from ui import datasource as ui_datasource
    monkeypatch.setattr(ui_datasource, "save_birth_date", lambda birth, cfg=None: (True, "保存した"))

    app.main()  # ボタンは押されていない＝バックテストの履歴取得も走らない
    assert "時価を今すぐ更新" in st.button_log   # 取得できる場所へ送る導線は残っていること
    assert "配当を今すぐ更新" in st.button_log


def test_saved_dividends_are_used_without_fetching(monkeypatch):
    """保存された div_annual が配当集計に効くこと（手入力 div_per_share が勝つこと）。"""
    _install_streamlit_stub(monkeypatch, checkbox_value=False)
    _reset_modules()
    import pricing_update as pu
    rows = [
        {"ticker": "1605", "div_per_share": "70", "div_annual": "62", "div_months": "3;9"},
        {"ticker": "VYM", "div_per_share": "", "div_annual": "550", "div_months": "3;6;9;12"},
    ]
    assert pu.dividend_map(rows) == {"1605": 70.0, "VYM": 550.0}
    assert pu.dividend_months_map(rows)["VYM"] == [3, 6, 9, 12]


def test_purpose_options_include_growth(monkeypatch):
    """用途の選択肢に「資産形成」があること。

    当初は日本個別株の高配当/優待を分ける列だったが、インデックス（オルカン等）は
    資産最大化が目的で配当も優待も当てはまらず、未分類のままになっていた。
    """
    _install_streamlit_stub(monkeypatch, checkbox_value=False)
    _reset_modules()
    from ui import constants as ui_constants

    assert ui_constants.PURPOSE_LABELS_BY_VALUE["growth"] == "資産形成"
    assert ui_constants.PURPOSE_LABELS[""] == "未分類"  # 取込直後の既定値は未分類のまま
    assert set(ui_constants.PURPOSE_LABELS_BY_VALUE) == {"dividend", "growth", "yutai"}


def _app_with_storage_stub(monkeypatch, saved: dict):
    """storage を書き込み記録用スタブに差し替えた ui.appdata を返す。"""
    _install_streamlit_stub(monkeypatch, checkbox_value=False)
    _reset_modules()
    import storage as sg

    monkeypatch.setattr(sg, "load", lambda cfg: (None, None))
    monkeypatch.setattr(
        sg, "save",
        lambda cfg, text, sha, msg: (saved.update(path=cfg.path, text=text), (True, "ok"))[1],
    )
    from ui import appdata
    return appdata, sg


def test_settings_are_written_to_their_own_files_not_holdings(monkeypatch):
    """設定の保存が holdings.csv を上書きしないこと。

    保存先の指定は path だけを差し替える実装なので、差し替えを忘れると**資産データを
    設定ファイルで丸ごと潰す**。取り返しがつかないため、パスを固定して守る。
    """
    saved: dict = {}
    appdata, sg = _app_with_storage_stub(monkeypatch, saved)
    cfg = sg.StorageConfig(token="t", owner="o", repo="r", path="holdings.csv")

    ok, _ = appdata.save_birth_date(date(1983, 8, 21), cfg)
    assert ok and saved["path"] == "user_settings.json"
    assert "1983-08-21" in saved["text"]

    saved.clear()
    ok, _ = appdata.save_view_orders(cfg, {"cols_holdings": ["銘柄"]})
    assert ok and saved["path"] == "view_settings.json"
    assert "cols_holdings" in saved["text"]


def test_birth_date_falls_back_to_local_file_without_storage(monkeypatch, tmp_path):
    # 保存先が未設定なら従来どおりローカルへ書く（ローカル起動を壊さない）
    saved: dict = {}
    appdata, _ = _app_with_storage_stub(monkeypatch, saved)
    monkeypatch.setattr(appdata, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(appdata, "SETTINGS_JSON", str(tmp_path / "user_settings.json"))

    ok, _ = appdata.save_birth_date(date(1983, 8, 21), None)
    assert ok and saved == {}  # storage へは書かない
    assert appdata.load_birth_date(None) == date(1983, 8, 21)


def test_save_buttons_appear_when_storage_is_configured(monkeypatch):
    """保存先が設定済みなら、各表に「この並びを保存」が出ること。

    show_table への cfg 引き渡しを落とすとボタンが静かに消える（`cfg is not None and
    st.button(...)` の短絡評価で描画自体が起きない）。実際にそれを踏み、画面を見るまで
    気付けなかった。ラベルが描画されたかで守る。
    """
    st = _run_main(monkeypatch, checkbox_value=False, secrets=STORAGE_SECRETS)
    assert "この並びを保存" in st.button_log
    assert "時価を今すぐ更新" in st.button_log  # 同じ理由で消えうる導線
    assert "生年月日を保存" in st.button_log


def test_no_save_button_without_storage(monkeypatch):
    # 保存先が無いときは出さない（押しても保存できないボタンを見せない）
    st = _run_main(monkeypatch, checkbox_value=False)
    assert "この並びを保存" not in st.button_log


def test_main_runs_with_every_allocation_axis(monkeypatch):
    """集計軸をどれに切り替えても main() が通ること。

    既定のスタブは options[0]（＝資産クラス）しか返さないため、後から足した軸は
    一度も実行されないまま緑になる。軸ごとに描画の分岐が違うので全部通す。

    **範囲は軸の一覧から取る**。以前は (0,1,2,3) とハードコードしていて、
    5軸目以降（口座区分・投資対象地域・企業規模）が実行されないまま緑になっていた。
    """
    _reset_modules()
    _install_streamlit_stub(monkeypatch, checkbox_value=False)
    from ui.tab_portfolio import ALLOCATION_AXES

    chosen = []
    for index in range(len(ALLOCATION_AXES)):
        st = _install_streamlit_stub(monkeypatch, checkbox_value=False, secrets=STORAGE_SECRETS)
        base_radio = st.radio

        def _radio(label, options, _i=index, **kw):
            if label != "集計軸":
                return base_radio(label, options, **kw)
            axis = options[_i]           # 範囲外なら IndexError で落とす（黙って先頭に戻さない）
            chosen.append(axis)
            return axis

        st.radio = _radio
        _reset_modules()
        import prices as pr
        import storage as sg
        monkeypatch.setattr(pr, "fetch_prices", lambda tickers: {})
        monkeypatch.setattr(pr, "fetch_dividends", lambda tickers: {})
        monkeypatch.setattr(pr, "fetch_dividend_months", lambda tickers: {})
        monkeypatch.setattr(pr, "fetch_fx_rate", lambda: None)
        monkeypatch.setattr(sg, "load", lambda cfg: (None, None))
        import app
        from ui import datasource as ui_datasource
        monkeypatch.setattr(ui_datasource, "save_birth_date", lambda birth, cfg=None: (True, "保存した"))
        app.main()

    # 回数ではなく「どの軸を踏んだか」で担保する
    assert chosen == list(ALLOCATION_AXES)


def test_uploaded_csv_keeps_storage_sha_for_replace_save(monkeypatch):
    """「置換」で取り込んだCSVを保存するとき、保存先の sha を引き継ぐこと。

    sha が None のまま PUT すると GitHub は既存ファイルへの上書きを **422** で弾く
    （実害：置換モードで保存できなかった）。表示する中身はアップロードCSVでも、
    sha は保存先の現在値でなければならない。
    """
    _install_streamlit_stub(monkeypatch, checkbox_value=False, secrets=STORAGE_SECRETS)
    _reset_modules()

    import storage as sg
    stored = "ticker,name,asset_class,shares,cost_per_share\n1605,INPEX,jp_dividend,10,1000\n"
    monkeypatch.setattr(sg, "load", lambda cfg: (stored, "sha-from-storage"))

    from ui import appdata
    uploaded = (
        "ticker,name,asset_class,shares,cost_per_share,industry\n"
        "9432,NTT,jp_dividend,100,150,情報・通信業\n"
    )
    rows, src, sha = appdata.load_rows(uploaded)

    assert [r["ticker"] for r in rows] == [9432]  # 中身はアップロードCSV
    assert src == "アップロードCSV"
    assert sha == "sha-from-storage"  # sha は保存先の現在値


def test_uploaded_csv_sha_is_none_without_storage(monkeypatch):
    """保存先未設定なら sha は None（新規作成扱い）。"""
    _install_streamlit_stub(monkeypatch, checkbox_value=False, secrets=None)
    _reset_modules()

    import storage as sg
    monkeypatch.setattr(sg, "load", lambda cfg: (None, None))

    from ui import appdata
    _, _, sha = appdata.load_rows(
        "ticker,name,asset_class,shares,cost_per_share\n9432,NTT,jp_dividend,100,150\n"
    )
    assert sha is None


def test_yen_short_keeps_kpi_values_readable(monkeypatch):
    """KPIバーは6列に並ぶため、円のフル桁だと途中で切れる（実際に切れた）。"""
    _install_streamlit_stub(monkeypatch, checkbox_value=False)
    _reset_modules()
    from ui.format import yen_short
    assert yen_short(11_138_875) == "¥1,114万"
    assert yen_short(4_181_867) == "¥418.2万"
    assert yen_short(9_122) == "¥9,122"      # 1万円未満は円のまま
    assert yen_short(0) == "¥0"
    assert len(yen_short(123_456_789)) <= 10  # 桁が増えても短いまま


# --- タブ構成とKPI（Phase 7 の再編）---


MAIN_TABS = ["概要", "配当", "インデックス", "収入計画", "資産・成績", "ポートフォリオ", "データ"]


def test_kpi_values_are_full_yen(monkeypatch):
    """主数字は円のフル桁で出す（万表記だと桁感が掴めない）。補足だけ万表記。"""
    _install_streamlit_stub(monkeypatch, checkbox_value=False)
    _reset_modules()
    from ui.format import yen, yen_short
    assert yen(15_240_000) == "¥15,240,000"
    assert yen_short(15_240_000) == "¥1,524万"


def test_load_goals_fills_keys_added_later(monkeypatch):
    """設定に項目が増えても落ちないこと。古い形の辞書がセッションに残る場合の回帰。"""
    st_stub = _install_streamlit_stub(monkeypatch, checkbox_value=False)
    _reset_modules()
    import dataio
    from ui import appdata
    from ui.constants import GOALS_STATE
    st_stub.session_state[GOALS_STATE] = {"goal_net_worth": 25_000_000.0}
    goals = appdata.load_goals(None)
    assert goals["goal_net_worth"] == 25_000_000.0          # 保存済みの値は残る
    assert set(goals) == set(dataio.DEFAULT_GOALS)          # 増えたキーは既定値で埋まる


def test_monthly_dividend_goal_is_derived_from_annual(monkeypatch):
    """月間目標は年間目標から導く。別々に持つと達成率と想定月収が食い違う。"""
    st_stub = _install_streamlit_stub(monkeypatch, checkbox_value=False)
    _reset_modules()
    import dataio
    import portfolio as pf
    from ui import appdata
    from ui.constants import GOALS_STATE
    from ui.kpi import plan_numbers
    # 年間60万・月間は矛盾した10万で保存されている状態
    st_stub.session_state[GOALS_STATE] = {
        **dataio.DEFAULT_GOALS, "goal_dividend_annual": 600_000.0,
        "goal_dividend_monthly": 100_000.0,
    }
    goals = appdata.load_goals(None)
    plan = plan_numbers(
        pf.build_holdings([], {}), {}, [], [], goals, date(1983, 8, 21)
    )
    # 月収目標は 配当5万（＝60万/12）＋取崩5万＋事業5万＋労働5万 ＝ 20万
    assert plan["goal_monthly"] == 200_000.0


def test_cf_target_uses_configured_goal(monkeypatch):
    """配当CFの到達判定は設定した目標で行う（月6万のハードコードだった）。"""
    st_stub = _install_streamlit_stub(monkeypatch, checkbox_value=False)
    _reset_modules()
    import simulation as sim
    from ui.tab_dividend import render_dividend_cf
    labels = []
    st_stub.metric = lambda label, *a, **kw: labels.append(label)
    render_dividend_cf(
        current_annual_dividend=600_000, purchase_yield=4.0, years=12,
        target_age=55, tax_rate=0.0, target_monthly=50_000,
    )
    assert any("月¥5.0万到達" in label for label in labels)
    assert not any("月¥6.0万到達" in label for label in labels)
    assert sim.TARGET_CF_MONTHLY_MIN == 60_000.0   # 既定値自体は据え置き


def test_main_tabs_are_rendered(monkeypatch):
    """6タブが作られること。構成を変えたらここが落ちる（意図した変更なら直す）。"""
    st = _run_main(monkeypatch, checkbox_value=False, secrets=STORAGE_SECRETS)
    assert MAIN_TABS in st.tab_log


def test_data_tab_has_every_editor(monkeypatch):
    """データタブの保存ボタンが全部出ること（保存先が設定済みの場合）。"""
    st = _run_main(monkeypatch, checkbox_value=False, secrets=STORAGE_SECRETS)
    for label in ("保存", "現金を保存", "収入を保存", "目標・前提値を保存",
                  "配当実績を保存", "今の状態を記録"):
        assert label in st.button_log


def test_runs_with_side_data_present(monkeypatch):
    """現金・スナップショット・配当実績がある状態でも通ること（推移・実績の描画経路）。"""
    st = _install_streamlit_stub(monkeypatch, checkbox_value=False, secrets=STORAGE_SECRETS)
    _reset_modules()

    import prices as pr
    import storage as sg
    for name in ("fetch_prices", "fetch_dividends", "fetch_dividend_months"):
        monkeypatch.setattr(pr, name, lambda tickers: {})
    monkeypatch.setattr(pr, "fetch_fx_rate", lambda: None)
    monkeypatch.setattr(sg, "save", lambda *a, **kw: (True, "保存しました。"))
    monkeypatch.setattr(sg, "check", lambda cfg: (True, "接続できました。"))
    monkeypatch.setattr(sg, "trigger_workflow", lambda *a, **kw: (True, "依頼しました。"))

    snapshots_csv = (
        "date,total_cost,total_market,gain,annual_dividend_pre_tax,annual_dividend_after_tax,"
        "index_pct,us_dividend_pct,jp_dividend_pct,reit_pct,cash,net_worth\n"
        "2026-08-01,6000000,9000000,3000000,400000,320000,50,20,25,5,1000000,10000000\n"
        "2026-09-01,6100000,9500000,3400000,420000,336000,50,20,25,5,1200000,10700000\n"
    )
    cash_csv = "name,amount,note\n楽天銀行,1200000,生活防衛\n"
    income_csv = ("month,category,amount,note\n"
                  "2026-08,事業,40000,\n"
                  "2026-09,労働収入,60000,\n")
    history_csv = ("date,ticker,name,gross,tax,net,account,source,note\n"
                   "2025-03-28,9432,NTT,5000,1016,3984,specific,sbi,\n"
                   "2026-03-27,9432,NTT,5500,0,5500,nisa_growth,sbi,\n")

    def fake_load(cfg):
        path = getattr(cfg, "path", "") if cfg else ""
        if path == "snapshots.csv":
            return (snapshots_csv, "sha")
        if path == "cash.csv":
            return (cash_csv, "sha")
        if path == "dividend_history.csv":
            return (history_csv, "sha")
        if path == "income.csv":
            return (income_csv, "sha")
        return (None, None)

    monkeypatch.setattr(sg, "load", fake_load)

    import app
    from ui import datasource as ui_datasource
    monkeypatch.setattr(ui_datasource, "save_birth_date", lambda birth, cfg=None: (True, "保存した"))
    app.main()  # 例外が出なければ成功
    assert MAIN_TABS in st.tab_log


def test_account_labels_cover_all_stored_values(monkeypatch):
    """画面の表示名が保存されうる値をすべて網羅していること。

    欠けると内部値（nisa_growth 等）がそのまま画面に出る。
    """
    _install_streamlit_stub(monkeypatch, checkbox_value=False)
    _reset_modules()
    import dataio
    from ui.constants import ACCOUNT_LABELS

    for value in dataio.ACCOUNTS:
        assert value in ACCOUNT_LABELS
    assert ACCOUNT_LABELS[""] == "特定"  # 空欄は特定扱い（税計算と揃える）
    assert ACCOUNT_LABELS["nisa_growth"] == "成長投資枠"


# --- st.fragment の構造ルール ---


def _fragment_functions() -> dict[str, ast.FunctionDef]:
    """ui/ 配下の @st.fragment が付いた関数を {名前: AST} で集める。"""
    import glob
    found: dict[str, ast.FunctionDef] = {}
    for path in glob.glob(os.path.join(SRC, "ui", "*.py")):
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for deco in node.decorator_list:
                target = deco.func if isinstance(deco, ast.Call) else deco
                if isinstance(target, ast.Attribute) and target.attr == "fragment":
                    found[node.name] = node
    return found


def test_fragments_are_not_nested():
    """フラグメントの入れ子を作らないこと。

    Streamlit は入れ子のフラグメントを許さず、**実行時に例外**になる。
    スタブは @st.fragment を素通しするためテストでは踏めず、画面を開くまで気付けない。
    構造として禁じる（例：_render_dividend_plan に付けると render_dividend_cf と入れ子になる）。
    """
    fragments = _fragment_functions()
    assert fragments, "ui/ に @st.fragment が1つも無い（付け忘れ or 検出の壊れ）"

    nested = [
        (name, call.func.id)
        for name, node in fragments.items()
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        and call.func.id in fragments and call.func.id != name
    ]
    assert nested == [], f"フラグメントが入れ子になっている：{nested}"


def test_saving_reruns_the_whole_app_not_just_the_fragment():
    """保存後の再実行は scope="app"。

    KPI帯（総資産・達成率）はタブより前に描画済みなので、既定の scope="fragment" では
    保存しても古い数字が残る。データタブの st.rerun はすべて app スコープであること。
    """
    source = open(os.path.join(SRC, "ui", "tab_data.py"), encoding="utf-8").read()
    calls = [n for n in ast.walk(ast.parse(source))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "rerun"]
    assert calls, "データタブに st.rerun が無い（保存しても画面が更新されない）"
    for call in calls:
        scopes = [kw.value.value for kw in call.keywords if kw.arg == "scope"]
        assert scopes == ["app"], f"{call.lineno}行目の st.rerun に scope='app' が無い"
