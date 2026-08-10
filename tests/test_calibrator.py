"""Tests for the self-improvement calibrator + DB lifecycle integration."""
from __future__ import annotations

import pytest

from brain.calibrator import apply_calibration, build_profile, compute_expectancy_by_type
from data.database import SignalDB
from engine.lifecycle import LifecycleError


def _seed_backtests(db: SignalDB) -> None:
    rows = [
        # Buy Pullback: strong positive expectancy (4 wins, 1 loss)
        *[{"ts": i, "symbol": "BTCUSDT", "timeframe": "15m", "plan_type": "Buy Pullback",
           "action": "BUY", "confidence_pct": 80, "horizon_hours": 4.0,
           "outcome": "FULL_WIN", "rr_achieved": 2.0, "max_favorable": 100.0,
           "max_adverse": -20.0, "entry": 60000.0, "trigger_level": None}
          for i in range(4)],
        {"ts": 9, "symbol": "BTCUSDT", "timeframe": "15m", "plan_type": "Buy Pullback",
         "action": "BUY", "confidence_pct": 80, "horizon_hours": 4.0,
         "outcome": "LOSS", "rr_achieved": -1.0, "max_favorable": 10.0,
         "max_adverse": -200.0, "entry": 60000.0, "trigger_level": None},
        # Breakout Buy: negative (1 win, 4 losses)
        {"ts": 20, "symbol": "BTCUSDT", "timeframe": "15m", "plan_type": "Breakout Buy",
         "action": "BUY", "confidence_pct": 60, "horizon_hours": 4.0,
         "outcome": "PARTIAL_WIN", "rr_achieved": 0.5, "max_favorable": 50.0,
         "max_adverse": -30.0, "entry": 61000.0, "trigger_level": 61200.0},
        *[{"ts": 20 + i, "symbol": "BTCUSDT", "timeframe": "15m", "plan_type": "Breakout Buy",
           "action": "BUY", "confidence_pct": 60, "horizon_hours": 4.0,
           "outcome": "LOSS", "rr_achieved": -1.0, "max_favorable": 5.0,
           "max_adverse": -150.0, "entry": 61000.0, "trigger_level": 61200.0}
          for i in range(1, 5)],
    ]
    db.save_backtest_rows(rows, run_id="test_calib")


def test_expectancy_computation(tmp_path):
    db = SignalDB(tmp_path / "t.db")
    _seed_backtests(db)
    stats = compute_expectancy_by_type(db)
    assert stats["Buy Pullback"]["expectancy"] == pytest.approx(1.4, abs=0.01)  # (2*4 - 1)/5
    assert stats["Breakout Buy"]["expectancy"] == pytest.approx(-0.7, abs=0.01)  # (0.5 - 4)/5
    db.close()


def test_build_profile_multipliers(tmp_path):
    db = SignalDB(tmp_path / "t.db")
    _seed_backtests(db)
    profile = build_profile(db, filter_neg=True, min_n=3)
    # Buy Pullback boosted (>1), Breakout Buy dampened (<1) or filtered
    bp = profile["Buy Pullback"]
    assert bp["multiplier"] > 1.0
    assert bp["samples"] == 5
    bb = profile["Breakout Buy"]
    assert bb["multiplier"] < 1.0
    db.close()


def test_apply_calibration():
    cal = {"Buy Pullback": {"multiplier": 1.2, "filtered": False, "samples": 50},
           "Breakout Buy": {"multiplier": 0.6, "filtered": False, "samples": 50},
           "Sell Pullback": {"multiplier": 1.0, "filtered": True, "samples": 40}}
    conf, filtered = apply_calibration(80, "Buy Pullback", cal)
    assert filtered is False and conf == 96
    conf, filtered = apply_calibration(60, "Breakout Buy", cal)
    assert filtered is False and conf == 36
    conf, filtered = apply_calibration(70, "Sell Pullback", cal)
    assert filtered is True
    # unknown / empty -> unchanged
    assert apply_calibration(70, "Nope", cal) == (70, False)
    assert apply_calibration(70, "Buy Pullback", {}) == (70, False)


def test_db_lifecycle_roundtrip(tmp_path):
    db = SignalDB(tmp_path / "t.db")
    from tests.test_database import _payload
    scan_id = db.save_scan(_payload())
    assert db.get_scan(scan_id)["status"] == "PENDING_REVIEW"
    assert db.update_status(scan_id, "APPROVED", note="looks good") == "APPROVED"
    assert db.update_status(scan_id, "EXECUTED") == "EXECUTED"
    assert db.update_status(scan_id, "CLOSED") == "CLOSED"
    with pytest.raises(LifecycleError):
        db.update_status(scan_id, "APPROVED")  # closed -> nothing
    history = db.decision_history(scan_id)
    assert len(history) == 3
    assert history[0]["to_state"] == "APPROVED"
    assert history[0]["note"] == "looks good"
    db.close()


def test_pending_reviews(tmp_path):
    db = SignalDB(tmp_path / "t.db")
    from tests.test_database import _payload
    p = _payload()
    db.save_scan(p)
    pending = db.pending_reviews()
    assert len(pending) == 1
    assert pending[0]["symbol"] == "BTCUSDT"
    db.close()


def test_calibration_save_load(tmp_path):
    db = SignalDB(tmp_path / "t.db")
    db.save_calibration({"Buy Pullback": {"multiplier": 1.2, "expectancy": 1.4,
                                          "samples": 50}})
    loaded = db.load_calibration()
    assert loaded["Buy Pullback"]["multiplier"] == 1.2
    db.close()


def test_regime_dimensioned_profile(tmp_path):
    """Decision B3: profiles are keyed by setup × regime."""
    from brain.calibrator import _decided_by_key, profile_key
    db = SignalDB(tmp_path / "reg.db")
    rows = [
        {"ts": i, "symbol": "BTCUSDT", "timeframe": "15m", "plan_type": "Buy Pullback",
         "action": "BUY", "confidence_pct": 80, "horizon_hours": 4.0,
         "outcome": "FULL_WIN", "rr_achieved": 2.0, "max_favorable": 1.0,
         "max_adverse": -1.0, "entry": 100.0, "trigger_level": None,
         "regime": "TRENDING_BULL"}
        for i in range(6)
    ]
    db.save_backtest_rows(rows, run_id="reg")
    stats = _decided_by_key(db)
    assert stats[profile_key("Buy Pullback", "TRENDING_BULL")]["backtest_samples"] == 6
    assert profile_key("Buy Pullback", "TRENDING_BULL") == "Buy Pullback::TRENDING_BULL"
    db.close()


def test_profile_proven_and_tp_rr(tmp_path):
    """Decisions B10/A2: proven flag requires 100 backtest + 20 paper samples;
    tp_rr is derived from the measured average winner."""
    from brain.calibrator import build_profile
    db = SignalDB(tmp_path / "reg.db")
    rows = [
        {"ts": i, "symbol": "BTCUSDT", "timeframe": "15m", "plan_type": "Buy Pullback",
         "action": "BUY", "confidence_pct": 80, "horizon_hours": 4.0,
         "outcome": "FULL_WIN", "rr_achieved": 2.5, "max_favorable": 1.0,
         "max_adverse": -1.0, "entry": 100.0, "trigger_level": None,
         "regime": "TRENDING_BULL"}
        for i in range(100)
    ]
    db.save_backtest_rows(rows, run_id="proven")
    for i in range(20):
        db.conn.execute(
            """INSERT INTO paper_trades(scan_id, signal_id, plan_type, symbol, timeframe,
                                        action, entry, stop_loss, take_profit, risk_reward,
                                        confidence_pct, status, created_ts, opened_ts,
                                        closed_ts, entry_price, exit_price, outcome,
                                        rr_achieved, close_reason, regime)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (10000 + i, f"P{i}", "Buy Pullback", "BTCUSDT", "15m", "BUY",
             100.0, 99.0, 102.0, 2.0, 70, "CLOSED", 1, 2, 3, 100.0, 102.0,
             "TP_HIT", 2.5, "test", "TRENDING_BULL"))
    db.conn.commit()
    profile = build_profile(db, min_n=100, min_paper_n=20)
    key = "Buy Pullback::TRENDING_BULL"
    assert profile[key]["proven"] is True
    assert profile[key]["backtest_samples"] == 100
    assert profile[key]["paper_samples"] == 20
    # avg winner 2.5R -> suggested TP 2.5 (within [1.5, 4.0])
    assert profile[key]["tp_rr"] == pytest.approx(2.5, abs=0.01)
    assert profile[key]["multiplier"] > 1.0
    db.close()


def test_suggest_tp_rr_by_type(tmp_path):
    from brain.calibrator import suggest_tp_rr_by_type
    profile = {
        "Buy Pullback::TRENDING_BULL": {"tp_rr": 2.5, "samples": 100},
        "Buy Pullback::RANGING": {"tp_rr": 1.6, "samples": 60},
        "Breakout Buy::TRENDING_BULL": {"tp_rr": None, "samples": 50},
    }
    by_type = suggest_tp_rr_by_type(profile)
    assert by_type["Buy Pullback"] == 2.5  # best-sampled wins
    assert "Breakout Buy" not in by_type
    reg = suggest_tp_rr_by_type(profile, regime="RANGING")
    assert reg["Buy Pullback"] == 1.6
