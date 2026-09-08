"""金額・増減の文字列化。全タブが同じ表記を使うためここに集める。"""
from __future__ import annotations


def yen(v: float) -> str:
    return f"¥{v:,.0f}"


def yen_short(v: float) -> str:
    """KPIバー用の短い金額表記（万円）。

    6項目を横一列に並べると、¥11,138,875 のような桁数は列幅に収まらず
    「¥11,13…」と切れる（実際に切れた）。万円単位なら3〜5文字で収まる。
    1万円未満は円のまま出す。
    """
    if abs(v) < 10000:
        return f"¥{v:,.0f}"
    man = v / 10000.0
    digits = 0 if abs(man) >= 1000 else 1
    return f"¥{man:,.{digits}f}万"


def delta_text(change: tuple[float, float] | None, unit: str = "") -> str | None:
    """スナップショットの差分を st.metric の delta 文字列にする。記録が無ければ None。"""
    if change is None:
        return None
    delta, rate = change
    sign = "+" if delta >= 0 else "-"
    return f"{sign}{yen_short(abs(delta))}{unit}（{rate:+.1f}%）"


def tone(value: float) -> str:
    """増減の色分け（プラス＝緑・マイナス＝臙脂）。"""
    return "up" if value >= 0 else "down"
