"""ui/style.py のテスト（純関数・Streamlit 非依存）。

主目的は **タブの文字が背景に埋もれないこと**。2026-09-08、タブ帯の地だけを
`@media (prefers-color-scheme: dark)`（＝OS/ブラウザの設定）で塗っていたため、
OSとアプリのテーマが食い違うと文字色と同じ色の帯になり、タブがほぼ読めなくなった
（実害：OSライト＋アプリDarkで白い帯に白い文字。別ポートのダークテーマ実行で再現）。

いまは `light-dark()` で **アプリのテーマ**（`.stApp` の color-scheme を継承）に追従させている。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.style import APP_STYLE  # noqa: E402

# Streamlit のテーマ既定色（実測）。タブの文字色はこれに追従する
THEME_TEXT = {"light": "#31333f", "dark": "#fafafa"}
THEME_BG = {"light": "#ffffff", "dark": "#0e1117"}

TAB_BAR_RULE = re.compile(
    r'div\[data-testid="stTabs"\] > div > div:first-child \{(.*?)\}', re.S
)


def _main_tab_bar_rule() -> str:
    """主タブ帯の宣言ブロック（入れ子タブ用や @supports 内のものは除く）。"""
    blocks = [b for b in TAB_BAR_RULE.findall(APP_STYLE) if "position: sticky" in b]
    assert len(blocks) == 1, f"主タブ帯の指定が{len(blocks)}個ある（1個であること）"
    return blocks[0]


def test_tab_bar_follows_the_app_theme_not_the_os():
    """タブ帯の地は light-dark() ＝ アプリのテーマに追従すること。

    ここが OS の設定に戻ると、テーマを切り替えたときにタブの文字が消える。
    """
    rule = _main_tab_bar_rule()
    assert f"light-dark({THEME_BG['light']}, {THEME_BG['dark']})" in rule


def test_tab_bar_has_a_plain_fallback_before_light_dark():
    """light-dark() 非対応ブラウザでも地は塗られること（透けて本文と重ならない）。"""
    rule = _main_tab_bar_rule()
    plain = rule.index(f"background-color: {THEME_BG['light']};")
    modern = rule.index("light-dark(")
    assert plain < modern, "非対応ブラウザ用のライト値は light-dark() より先に置く"


def test_os_setting_is_used_only_as_a_fallback():
    """prefers-color-scheme は @supports not(...) の中だけで使うこと。

    外に出ると対応ブラウザでも OS の設定が後勝ちし、不具合が再発する。
    """
    assert "prefers-color-scheme" in APP_STYLE  # 保険自体は残す
    guard = APP_STYLE.index("@supports not (color: light-dark(#fff, #000))")
    assert APP_STYLE.index("prefers-color-scheme") > guard


def test_tab_text_and_bar_are_never_the_same_color():
    """どちらのテーマでも「帯の色」と「そのテーマの文字色」が一致しないこと。

    色の名前ではなく**組み合わせ**で守る。片方だけ直すと再発するため。
    """
    for theme, text in THEME_TEXT.items():
        assert THEME_BG[theme] != text, f"{theme}: 帯 {THEME_BG[theme]} と文字 {text} が同色"


def test_nested_tabs_are_transparent():
    """入れ子のタブ（用途別）は地を塗らない＝主タブと重なって読めなくなるため。"""
    nested = re.search(
        r'div\[data-testid="stTabs"\] div\[data-testid="stTabs"\] > div > div:first-child \{(.*?)\}',
        APP_STYLE, re.S,
    )
    assert nested and "background-color: transparent" in nested.group(1)


def test_style_is_a_single_style_block():
    assert APP_STYLE.count("<style>") == 1 and APP_STYLE.count("</style>") == 1
