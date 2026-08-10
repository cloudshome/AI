"""Tests for the professional journal + business metrics (decisions B5, B4)."""
from __future__ import annotations

import time

import pytest

from data.database import SignalDB
from brain.journal import (save_journal, execution_quality, violation_rate,
                           pre_trade_checklist, describe_entry)
from brain.metrics import business_metrics


def _decided(db: SignalDB, rr: float, n: int = 1, start_id: int = 8000) -> None:
    ts = int(time.time() * 1000)
    for i in range(n):
        outcome = "TP_HIT" if rr > 0 else "STOP_LOSS"
        db.conn.execute(
            """INSERT INTO paper_trades(scan_id, signal_id, plan_type, symbol, timeframe,
                                        action, entry, stop_loss, take_profit, risk_reward,
                                        confidence_pct, status, created_ts, opened_ts,
                                        closed_ts, entry_price, exit_price, outcome,
                                        rr_achieved, close_reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (start_id + i, f"SIG{start_id+i}", "Buy Pullback", "BTCUSDT", "15m",
             "BUY", 100.0, 99.0, 102.0, 2.0, 70, "CLOSED", ts, ts, ts,
             100.0, 99.0 if rr < 0 else 102.0, outcome, rr, "test"))
    db.conn.commit()


def test_journal_roundtrip(tmp_path):
    """Decision B5: post-trade fields persist per scan."""
    db = SignalDB(tmp_path / "j.db")
    entry = save_journal(db, 42, followed_rules=True, emotion="calm",
                         mistake="none", notes="textbook sweep")
    assert entry["followed_rules"] == 1
    assert entry["emotion"] == "calm"
    again = save_journal(db, 42, followed_rules=False, emotion="greedy")
    assert again["followed_rules"] == 0  # upsert, not duplicate
    assert again["emotion"] == "greedy"
    db.close()


def test_execution_quality_verdicts(tmp_path):
    """A profitable rule-breaking trade is terrible; a losing disciplined
    trade is excellent (your roadmap's core idea)."""
    db = SignalDB(tmp_path / "j.db")
    _decided(db, -1.0, n=1, start_id=8000)  # a loss
    _decided(db, 2.0, n=1, start_id=8001)   # a win
    save_journal(db, 8000, followed_rules=True)
    q1 = execution_quality(db, 8000)
    assert "Excellent" in q1["verdict"] and q1["quality"] == "B+"
    save_journal(db, 8001, followed_rules=False)
    q2 = execution_quality(db, 8001)
    assert "Terrible" in q2["verdict"] and "luck" in q2["verdict"]
    db.close()


def test_violation_rate(tmp_path):
    db = SignalDB(tmp_path / "j.db")
    save_journal(db, 1, followed_rules=True)
    save_journal(db, 2, followed_rules=False)
    save_journal(db, 3, followed_rules=True)
    v = violation_rate(db)
    assert v["n"] == 3 and v["violations"] == 1
    assert v["violation_rate"] == pytest.approx(1 / 3, abs=0.001)
    db.close()


def test_pre_trade_checklist(tmp_path):
    payload = {
        "signal": {"action": "BUY", "asset": "BTCUSDT", "timeframe": "15m",
                   "entry": 100.0, "stop_loss": 99.0, "take_profit": 103.0,
                   "risk_reward": 2.0},
        "plans": [{"type": "Buy Pullback"}],
        "snapshot": {"features": {"symbol": "BTCUSDT", "regime_label": "Trending Bull"}},
        "mtf": {"htf_bias": "bullish"},
        "context": {"macro": {"label": "normal"}},
    }
    c = pre_trade_checklist(payload)
    assert c["setup"] == "Buy Pullback"
    assert c["regime"] == "Trending Bull"
    assert c["planned_rr"] == 2.0
    assert c["news_environment"] == "normal"


def test_business_metrics(tmp_path):
    """Decision B4: profit factor, max drawdown, streaks, rolling windows."""
    db = SignalDB(tmp_path / "m.db")
    # 6 wins of +2R then 4 losses of -1R → PF = 12/4 = 3, wr 60%
    _decided(db, 2.0, n=6, start_id=1)
    _decided(db, -1.0, n=4, start_id=100)
    m = business_metrics(db)
    o = m["overall"]
    assert o["n"] == 10
    assert o["win_rate"] == 0.6
    assert o["profit_factor"] == 3.0
    assert o["max_win_streak"] == 6
    assert o["max_loss_streak"] == 4
    assert o["expectancy_r"] == pytest.approx(0.8)
    assert m["rolling"]["50"]["n"] == 10  # window smaller than the sample: uses the tail
    assert m["rolling"]["100"]["n"] == 10
    db.close()


def test_business_metrics_drawdown(tmp_path):
    db = SignalDB(tmp_path / "m.db")
    _decided(db, -1.0, n=25, start_id=1)   # -25 * 0.25% = -6.25% drawdown
    m = business_metrics(db)
    assert m["overall"]["max_drawdown_pct"] == pytest.approx(6.25, abs=0.01)
    db.close()


def test_describe_entry():
    txt = describe_entry({"followed_rules": 1, "emotion": "calm"})
    assert "YES" in txt
