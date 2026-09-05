"""viewsettings.py のテスト（純関数・外部依存なし）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import viewsettings as vs  # noqa: E402


# --- 相互変換 ---


def test_orders_roundtrip():
    orders = {"cols_holdings": ["銘柄", "評価額", "含み損益"], "cols_drift": ["資産クラス"]}
    assert vs.parse_orders(vs.serialize_orders(orders)) == orders


def test_parse_returns_empty_for_unset_or_broken():
    """設定ファイルは手編集で壊れうる。並び順のために画面を落とさない。"""
    assert vs.parse_orders(None) == {}
    assert vs.parse_orders("") == {}
    assert vs.parse_orders("{壊れたJSON") == {}
    assert vs.parse_orders('{"column_orders": "文字列"}') == {}
    assert vs.parse_orders('{"別のキー": {}}') == {}


def test_parse_skips_non_list_values():
    got = vs.parse_orders('{"column_orders": {"a": ["x"], "b": 3}}')
    assert got == {"a": ["x"]}


# --- 現在の列に合わせた解決 ---


def test_resolve_uses_saved_order():
    assert vs.resolve_order(["評価額", "銘柄"], ["銘柄", "評価額"]) == ["評価額", "銘柄"]


def test_resolve_falls_back_to_current_columns():
    assert vs.resolve_order(None, ["銘柄", "評価額"]) == ["銘柄", "評価額"]
    assert vs.resolve_order([], ["銘柄", "評価額"]) == ["銘柄", "評価額"]


def test_resolve_drops_columns_that_no_longer_exist():
    # 列名を変えた・集計軸を変えた場合。存在しない列を column_order に渡さない
    assert vs.resolve_order(["旧列", "銘柄"], ["銘柄", "評価額"]) == ["銘柄", "評価額"]


def test_resolve_appends_new_columns_at_the_end():
    """列を追加したとき、保存済みの並びのせいで新しい列が見えなくなるのを防ぐ。

    利用者が意図的に外した列と、保存後に増えた列は区別できないため、
    見落としを防ぐ側（必ず見える）に倒している。
    """
    assert vs.resolve_order(["銘柄"], ["銘柄", "評価額", "用途"]) == ["銘柄", "評価額", "用途"]
