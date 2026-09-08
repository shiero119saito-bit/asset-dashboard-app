"""FIRE STATION の画面（Streamlit UI）。

app.py は main() だけを持ち、描画はこのパッケージに置く。
依存は一方向（app.py → tab_* → widgets/format/appdata/cache/constants）で、
集計・計算の純関数（portfolio / dividend / simulation 等）はここに持ち込まない。
"""
