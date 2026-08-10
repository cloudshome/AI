"""Tests for the walk-forward backtester (offline, synthetic data)."""
from __future__ import annotations

from data.backtester import (
    OUTCOMES, WIN_SET, GradedPlan, _aggregate, _evaluate, run_backtest,
)


def test_aggregate_empty():
    agg = _aggregate([])
    assert agg["executed"] == 0 and agg["win_rate"] is None


def test_aggregate_win_rate():
    graded = [
        GradedPlan(ts=1, plan_type="Immediate Buy", action="BUY", confidence_pct=80,
                   entry=100.0, trigger_level=None, horizon_hours=4.0, outcome="FULL_WIN",
                   rr_achieved=2.0),
        GradedPlan(ts=2, plan_type="Immediate Buy", action="BUY", confidence_pct=60,
                   entry=100.0, trigger_level=None, horizon_hours=4.0, outcome="LOSS",
                   rr_achieved=-1.0),
        GradedPlan(ts=3, plan_type="Buy Pullback", action="BUY", confidence_pct=90,
                   entry=100.0, trigger_level=98.0, horizon_hours=4.0, outcome="NOT_TRIGGERED"),
    ]
    agg = _aggregate(graded)
    assert agg["executed"] == 2
    assert agg["wins"] == 1 and agg["losses"] == 1
    assert agg["not_triggered"] == 1
    assert agg["win_rate"] == 0.5
    assert agg["expectancy"] == 0.5  # (2.0 + -1.0) / 2


def test_evaluate_buy_win():
    """BUY plan where price first hits TP1 then TP2."""
    import pandas as pd
    df = pd.DataFrame({
        "ts": [1, 2, 3, 4, 5],
        "open": [100, 102, 104, 106, 108],
        "high": [101, 103, 105, 107, 109],
        "low": [99, 101, 103, 105, 107],
        "close": [100, 102, 104, 106, 108],
        "volume": [10, 10, 10, 10, 10],
    })
    plan = {"type": "Immediate Buy", "action": "BUY", "entry": 100.0,
            "stop_loss": 97.0, "take_profits": [104.0, 108.0],
            "confidence": 80, "trigger_level": None}
    gp = _evaluate(plan, df, i=0, horizon_bars=10)
    assert gp.outcome == "FULL_WIN"
    assert gp.rr_achieved > 0


def test_evaluate_sell_loss():
    """SELL plan where price hits SL (above entry) first."""
    import pandas as pd
    df = pd.DataFrame({
        "ts": [1, 2, 3],
        "open": [100, 102, 104],
        "high": [101, 103, 105],
        "low": [99, 101, 103],
        "close": [100, 102, 104],
        "volume": [10, 10, 10],
    })
    plan = {"type": "Immediate Sell", "action": "SELL", "entry": 100.0,
            "stop_loss": 103.0, "take_profits": [97.0],
            "confidence": 70, "trigger_level": None}
    gp = _evaluate(plan, df, i=0, horizon_bars=5)
    assert gp.outcome == "LOSS"
    assert gp.rr_achieved == -1.0


def test_evaluate_not_triggered():
    """Waiting plan whose entry level is never reached."""
    import pandas as pd
    df = pd.DataFrame({
        "ts": [1, 2, 3],
        "open": [100, 101, 102],
        "high": [101, 102, 103],
        "low": [99, 100, 101],
        "close": [100, 101, 102],
        "volume": [10, 10, 10],
    })
    plan = {"type": "Buy Pullback", "action": "BUY", "entry": 95.0,
            "stop_loss": 92.0, "take_profits": [102.0],
            "confidence": 88, "trigger_level": 95.0, "status": "waiting"}
    gp = _evaluate(plan, df, i=0, horizon_bars=5)
    assert gp.outcome == "NOT_TRIGGERED"


def test_run_backtest_shape(df):
    result = run_backtest(df, symbol="BTCUSDT", timeframe="15m",
                          horizons=[1.0], min_bars=100, step=20,
                          min_confidence=0)
    report = result["report"]
    assert report["meta"]["symbol"] == "BTCUSDT"
    assert report["meta"]["timeframe"] == "15m"
    assert "summary" in report and "per_horizon" in report
    assert "by_type" in report and "by_action" in report
    # every graded outcome is one of the allowed set
    for g in result["graded"]:
        assert g.outcome in OUTCOMES
    assert report["summary"]["win_rate"] is None or 0 <= report["summary"]["win_rate"] <= 1


def test_backtest_tags_regime(tmp_path):
    """Decision B3: backtest rows carry the regime at signal time."""
    from tests.conftest import make_ohlcv
    df = make_ohlcv(n=300, seed=11)
    out = run_backtest(df, symbol="BTCUSDT", timeframe="15m", horizons=[1.0],
                       min_bars=150, step=5)
    graded = out["graded"]
    assert graded, "expected some graded plans"
    assert any(g.regime for g in graded)
    assert "by_regime" in out["report"]
    rows = [g.as_row() for g in graded]
    assert all("regime" in r for r in rows)
