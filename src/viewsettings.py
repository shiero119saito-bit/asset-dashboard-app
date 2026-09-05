"""画面の表示設定（表ごとの列の並び順）の相互変換（純関数）。

Streamlit の `session_state` はブラウザを閉じると消えるため、列の並びを毎回やり直す
ことになる。保存先（private repo）に置いて端末をまたいで持ち回れるようにする。

`storage.py` と組み合わせて使うが、このモジュール自体は I/O を持たない
（dataio.py と同じ方針＝読み書きは app.py 側）。
"""
from __future__ import annotations

import json

ORDERS_KEY = "column_orders"


def parse_orders(text: str | None) -> dict[str, list[str]]:
    """設定JSON文字列から {表のキー: 列名リスト} を取り出す。

    設定ファイルは手で壊れうる（手編集・空ファイル・列名の変更）ため、例外を投げず
    空 dict を返して「未設定」として扱う。画面を落とすほどの情報ではない。
    """
    if not text or not text.strip():
        return {}
    try:
        raw = json.loads(text).get(ORDERS_KEY)
    except (ValueError, TypeError, AttributeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): [str(col) for col in value]
        for key, value in raw.items()
        if isinstance(value, list)
    }


def serialize_orders(orders: dict[str, list[str]]) -> str:
    """列順の設定を JSON 文字列にする（parse_orders の逆）。"""
    return json.dumps({ORDERS_KEY: orders}, ensure_ascii=False, indent=2)


def resolve_order(saved: list[str] | None, columns: list[str]) -> list[str]:
    """保存された並びを、いまの表の列に合わせて解決する。

    保存後に列が増減しても壊れないようにする：
    - 保存値のうち**いま存在しない列は落とす**（列名を変えた・集計軸を変えた場合）
    - 保存値に**無い列は末尾に足す**（列を追加した場合に見えなくならないようにする）

    ただし利用者が意図的に外した列まで復活させると「非表示」が機能しないため、
    復活させるのは保存時に存在しなかった新しい列だけ、という区別はできない。
    ここでは**見落としを防ぐ側**に倒す（増えた列は必ず見える）。
    """
    if not saved:
        return list(columns)
    kept = [col for col in saved if col in columns]
    added = [col for col in columns if col not in kept]
    return kept + added
