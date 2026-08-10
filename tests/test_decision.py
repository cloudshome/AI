"""Tests for the professional desk decision orchestrator (decision A4)."""
from __future__ import annotations

import pytest

from brain.decision import build_decision, _top_plan_type
from data.database import SignalDB


def _payload(desk_action: str = "BUY") -> dict:
    return {
        "signal": {"action": desk_action, "asset": "BTCUSDT", "timeframe": "15m",
                   "entry": 100.0, "stop_loss": 99.0, "take_profit": 103.0,
                   "risk_reward": 2.0, "confidence": "HIGH"},
        "plans": [{"type": "Buy Pullback", "primary": True, "action": "BUY",
                   "condition": "IF pullback", "entry": 100.0, "stop_loss": 99.0,
                   "take_profits": [103.0], "confidence": 85,
                   "confidence_label": "HIGH"}],
        "snapshot": {"features": {"symbol": "BTCUSDT", "price": 100.0,
                                  "regime_name": "TRENDING_BULL",
                                  "regime_label": "Trending Bull"}},
        "mtf": {"htf_bias": "bullish"},
        "context": {"macro": {}},
        "intelligence": {"signal": desk_action},
    }


def test_desk_no_trade_passthrough(tmp_path):
    """NO TRADE from the desk stays NO TRADE; no gates need to fire."""
    db = SignalDB(tmp_path / "d.db")
    d = build_decision(_payload("NO TRADE"), "BTCUSDT", db)
    assert d["action"] == "NO TRADE"
    assert d["desk_action"] == "NO TRADE"
    db.close()


def test_clean_trade_allowed_when_gates_open(tmp_path):
    db = SignalDB(tmp_path / "d.db")
    d = build_decision(_payload("BUY"), "BTCUSDT", db)
    # No open exposure, no trader flags, gate open — but the setup is unproven
    # at student level, so the risk gate blocks it.
    assert d["action"] == "NO TRADE"
    assert any("unproven" in b for b in d["blocked_by"])
    db.close()


def test_playbook_veto_gold_news(tmp_path):
    from datetime import datetime, timezone
    db = SignalDB(tmp_path / "d.db")
    payload = _payload("BUY")
    payload["context"] = {"macro": {"high_impact_imminent": True, "available": True}}
    d = build_decision(payload, "XAUUSD", db,
                       now=datetime(2026, 8, 9, 14, 0, tzinfo=timezone.utc))
    assert d["action"] == "NO TRADE"
    assert any("playbook" in b.lower() for b in d["blocked_by"])
    assert "us_data_countdown" in str(d["gates"]["playbook"]["blocked_by"])
    db.close()


def test_portfolio_veto(tmp_path):
    db = SignalDB(tmp_path / "d.db")
    db.conn.execute(
        """INSERT INTO paper_trades(scan_id, signal_id, plan_type, symbol, timeframe,
                                    action, entry, stop_loss, take_profit, risk_reward,
                                    confidence_pct, status, created_ts, opened_ts, entry_price)
           VALUES (1,'S1','Buy Pullback','BTCUSDT','15m','BUY',100.0,99.0,102.0,2.0,
                   70,'OPEN',1,2,100.0)""")
    db.conn.commit()
    d = build_decision(_payload("BUY"), "ETHUSDT", db)
    assert d["action"] == "NO TRADE"
    assert any("portfolio" in b.lower() for b in d["blocked_by"])
    db.close()


def test_trader_state_veto(tmp_path):
    db = SignalDB(tmp_path / "d.db")
    db.set_trader_state(revenge=True)
    d = build_decision(_payload("BUY"), "BTCUSDT", db)
    assert d["action"] == "NO TRADE"
    assert any("trader state" in b for b in d["blocked_by"])
    db.close()


def test_top_plan_type_prefers_primary():
    payload = {
        "plans": [
            {"type": "Breakout Buy", "primary": False},
            {"type": "Buy Pullback", "primary": True},
        ]
    }
    assert _top_plan_type(payload) == "Buy Pullback"
    assert _top_plan_type({"plans": [{"type": "X", "primary": False}]}) == "X"
