"""simulation.py の純関数テスト。価格履歴・前提値はすべて注入し通信なしで実行。"""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import simulation as sim  # noqa: E402


# --- 年齢計算 ---


def test_age_before_on_and_after_birthday():
    birth = date(1980, 6, 15)
    assert sim.age_at(birth, date(2026, 6, 14)) == 45  # 誕生日前日
    assert sim.age_at(birth, date(2026, 6, 15)) == 46  # 当日
    assert sim.age_at(birth, date(2026, 6, 16)) == 46  # 翌日


def test_age_leap_day_birth_in_common_year():
    # 2/29生まれは平年では3/1に歳を取る（2/28時点ではまだ前の年齢）
    birth = date(2000, 2, 29)
    assert sim.age_at(birth, date(2026, 2, 28)) == 25
    assert sim.age_at(birth, date(2026, 3, 1)) == 26


def test_years_until_age_clamps_at_zero():
    birth = date(1970, 1, 1)
    today = date(2026, 9, 2)  # 56歳
    assert sim.years_until_age(birth, today, 55) == 0  # 既に超過
    assert sim.years_until_age(birth, today, 60) == 4


# --- 積立シミュレーション ---


def test_accumulation_zero_return_equals_principal():
    # 利回り0%なら評価額は投下元本と一致する（線形）
    points = sim.project_accumulation(initial=1_000_000, monthly=100_000, years=2, annual_return=0.0)
    assert len(points) == 3  # 0年目〜2年目
    last = points[-1]
    assert last.principal == pytest.approx(1_000_000 + 100_000 * 24)
    assert last.value == pytest.approx(last.principal)


def test_accumulation_compound_exceeds_principal():
    points = sim.project_accumulation(initial=1_000_000, monthly=100_000, years=10, annual_return=5.0)
    last = points[-1]
    assert last.value > last.principal  # 複利で元本を上回る
    assert points[0].year == 0 and points[0].value == 1_000_000


def test_accumulation_zero_years_returns_current_only():
    points = sim.project_accumulation(initial=500_000, monthly=130_000, years=0, annual_return=5.0)
    assert len(points) == 1
    assert points[0].value == 500_000


def test_accumulation_no_contribution_is_pure_compound():
    # 積立0＝初期資産の複利のみ。1年で概ね年率どおり増える
    points = sim.project_accumulation(initial=1_000_000, monthly=0, years=1, annual_return=12.0)
    assert points[-1].value == pytest.approx(1_000_000 * (1.01**12), rel=1e-9)
    assert points[-1].principal == 1_000_000


# --- 配当CF ---


def test_dividend_cf_grows_and_after_tax_is_smaller():
    points = sim.project_dividend_cf(
        current_annual_dividend=200_000,
        monthly=130_000,
        years=5,
        dividend_yield=4.0,
        dividend_growth=3.0,
        income_ratio=0.4,
    )
    assert points[0].annual_pre_tax == 200_000
    assert points[-1].annual_pre_tax > points[0].annual_pre_tax  # 増配＋買い増しで成長
    assert points[-1].annual_after_tax < points[-1].annual_pre_tax  # 税で目減り


def test_dividend_cf_first_year_math():
    # 1年目 = 既存200,000×1.03 + 買付(130,000×12×0.5)×5% = 206,000 + 39,000
    points = sim.project_dividend_cf(
        current_annual_dividend=200_000,
        monthly=130_000,
        years=1,
        dividend_yield=5.0,
        dividend_growth=3.0,
        income_ratio=0.5,
    )
    assert points[1].annual_pre_tax == pytest.approx(206_000 + 39_000)


def test_dividend_monthly_is_annual_over_12():
    points = sim.project_dividend_cf(
        current_annual_dividend=120_000, monthly=0, years=0,
        dividend_yield=4.0, dividend_growth=0.0, income_ratio=0.4,
    )
    assert points[0].monthly_pre_tax == pytest.approx(10_000)


def test_first_year_reaching_target():
    points = sim.project_dividend_cf(
        current_annual_dividend=0,
        monthly=130_000,
        years=30,
        dividend_yield=4.0,
        dividend_growth=3.0,
        income_ratio=0.4,
    )
    year = sim.first_year_reaching(points, sim.TARGET_CF_MONTHLY_MIN)
    assert year is not None and year > 0
    # 税抜判定の方が厳しい＝税込判定より到達が遅い（または同年）
    year_pre_tax = sim.first_year_reaching(points, sim.TARGET_CF_MONTHLY_MIN, pre_tax=True)
    assert year_pre_tax <= year


def test_first_year_reaching_returns_none_when_unreachable():
    points = sim.project_dividend_cf(
        current_annual_dividend=0, monthly=0, years=10,
        dividend_yield=4.0, dividend_growth=3.0, income_ratio=0.4,
    )
    assert sim.first_year_reaching(points, sim.TARGET_CF_MONTHLY_MIN) is None


# --- バックテスト ---


def test_backtest_matches_hand_calculation():
    # 1銘柄・価格100→200、毎月10,000円を2ヶ月積立
    # 1ヶ月目: 100株購入(10,000/100)、評価額10,000
    # 2ヶ月目: 50株購入(10,000/200)、保有150株、評価額30,000、元本20,000
    history = {"A": [(date(2026, 1, 31), 100.0), (date(2026, 2, 28), 200.0)]}
    result = sim.backtest_dca(history, {"A": 1.0}, monthly=10_000)
    assert result.invested == pytest.approx(20_000)
    assert result.final_value == pytest.approx(30_000)
    assert result.return_rate == pytest.approx(50.0)
    assert result.months == 2


def test_backtest_uses_only_common_dates():
    # B は1月分の価格がない → 共通月（2月）だけが対象
    history = {
        "A": [(date(2026, 1, 31), 100.0), (date(2026, 2, 28), 100.0)],
        "B": [(date(2026, 2, 28), 50.0)],
    }
    result = sim.backtest_dca(history, {"A": 0.5, "B": 0.5}, monthly=10_000)
    assert result.months == 1
    assert result.invested == pytest.approx(10_000)
    assert result.final_value == pytest.approx(10_000)  # 買った瞬間＝取得額と一致


def test_backtest_weights_are_normalized():
    # 合計が1でない比率でも比率として扱う（60:20 → 75%:25%）
    history = {
        "A": [(date(2026, 1, 31), 100.0)],
        "B": [(date(2026, 1, 31), 100.0)],
    }
    result = sim.backtest_dca(history, {"A": 60.0, "B": 20.0}, monthly=10_000)
    assert result.final_value == pytest.approx(10_000)
    assert result.invested == pytest.approx(10_000)


def test_backtest_worst_unrealized_rate_after_decline():
    # 100 → 50 に半減。1月:元本10,000/評価10,000（0%）、2月:元本20,000/評価15,000（-25%）
    history = {"A": [(date(2026, 1, 31), 100.0), (date(2026, 2, 28), 50.0)]}
    result = sim.backtest_dca(history, {"A": 1.0}, monthly=10_000)
    assert result.worst_unrealized_rate == pytest.approx(-25.0)
    assert result.return_rate < 0


def test_backtest_worst_unrealized_rate_is_zero_when_never_negative():
    # 一貫して値上がり＝含み損が発生しない
    history = {"A": [(date(2026, 1, 31), 100.0), (date(2026, 2, 28), 200.0)]}
    result = sim.backtest_dca(history, {"A": 1.0}, monthly=10_000)
    assert result.worst_unrealized_rate == 0.0


def test_find_discontinuous_detects_unadjusted_split():
    # 実例：2559 は分割未調整で月次 -80% の段差が出る（13,920円 → 2,995円）
    history = {
        "split": [(date(2026, 1, 31), 13_920.0), (date(2026, 2, 28), 2_995.0)],
        "normal": [(date(2026, 1, 31), 1_000.0), (date(2026, 2, 28), 1_050.0)],
    }
    assert sim.find_discontinuous(history) == ["split"]


def test_find_discontinuous_detects_spike():
    history = {"spike": [(date(2026, 1, 31), 100.0), (date(2026, 2, 28), 250.0)]}
    assert sim.find_discontinuous(history) == ["spike"]


def test_find_discontinuous_allows_severe_but_realistic_crash():
    # 月次 -30% は暴落として起こりうる＝除外しない
    history = {"crash": [(date(2026, 1, 31), 100.0), (date(2026, 2, 28), 70.0)]}
    assert sim.find_discontinuous(history) == []


def test_find_discontinuous_empty_and_single_point_are_safe():
    assert sim.find_discontinuous({}) == []
    assert sim.find_discontinuous({"A": [(date(2026, 1, 31), 100.0)]}) == []
    assert sim.find_discontinuous({"A": [(date(2026, 1, 31), 0.0), (date(2026, 2, 28), 5.0)]}) == []


def test_backtest_empty_history_is_safe():
    assert sim.backtest_dca({}, {"A": 1.0}, monthly=10_000).series == []
    assert sim.backtest_dca({"A": []}, {"A": 1.0}, monthly=10_000).invested == 0.0
    # weights に無い銘柄しか無い場合も安全
    assert sim.backtest_dca({"Z": [(date(2026, 1, 31), 10.0)]}, {"A": 1.0}, 10_000).series == []


def test_backtest_zero_price_month_is_skipped_safely():
    # 価格0の銘柄で ZeroDivisionError にならないこと
    history = {"A": [(date(2026, 1, 31), 0.0), (date(2026, 2, 28), 100.0)]}
    result = sim.backtest_dca(history, {"A": 1.0}, monthly=10_000)
    assert result.invested == pytest.approx(20_000)
    assert result.final_value == pytest.approx(10_000)  # 2ヶ月目の買付分のみ
