# Asset Dashboard

保有資産の見える化と配当管理を行う Streamlit アプリ。

## 機能
- 保有資産の見える化：評価額・含み損益・構成比・目標アセットアロケーションとのズレ
- 配当管理：年間配当（税込/税抜）・取得額/評価額ベース利回り・権利確定月別・セクター別/日米別

## ローカル実行
```
pip install -r requirements.txt
streamlit run src/app.py
```

## データソース（優先順）
1. サイドバーのCSVアップロード
2. `st.secrets["holdings"]["csv"]`（デプロイ時の保有データ）
3. `data/holdings.csv`（ローカル・gitignore）
4. `data/holdings.sample.csv`（サンプル）

保有CSVの列：`ticker, name, asset_class, shares, cost_per_share, sector, market, div_per_share`
（asset_class: index / us_dividend / jp_dividend / reit、market: jp / us）

## テスト
```
python -m pytest src/tests/ -v
```

## 注意
実保有データ（銘柄・金額）はこのリポジトリに含めない。デプロイ時は Streamlit Secrets に格納し、閲覧は viewer 許可リストで限定する。
