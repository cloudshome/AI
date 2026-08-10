"""End-to-end tests for the signal engine + schema validation."""
from __future__ import annotations

import re

from engine.signal_engine import analyze_frame
from output.signal_schema import validate_signal, validate_plan, validate_output


def test_full_pipeline(df):
    out = analyze_frame(df, symbol="BTCUSDT", timeframe="15m", min_confidence=0)
    sig = out.best_signal
    assert sig["asset"] == "BTCUSDT"
    assert sig["timeframe"] == "15m"
    assert sig["action"] in ("BUY", "SELL", "NO TRADE")
    assert re.match(r"^BTCUSDT_\d{8}_\d{4}$", sig["signal_id"])
    assert isinstance(sig["timestamp"], int)
    assert sig["confidence"] in ("HIGH", "MEDIUM", "LOW", "NO TRADE")
    assert isinstance(sig["reason"], str) and len(sig["reason"]) > 5


def test_signal_validation_ok():
    sig = {
        "signal_id": "BTCUSDT_20260804_1452", "timestamp": 1722779520000,
        "asset": "BTCUSDT", "action": "BUY", "entry": 61250.0,
        "stop_loss": 60700.0, "take_profit": 62200.0, "risk_reward": 2.1,
        "confidence": "HIGH", "timeframe": "15m", "reason": "test",
    }
    assert validate_signal(sig) == []


def test_signal_validation_catches_bad_sl():
    sig = {
        "signal_id": "BTCUSDT_20260804_1452", "timestamp": 1,
        "asset": "BTCUSDT", "action": "BUY", "entry": 100.0,
        "stop_loss": 120.0, "take_profit": 130.0, "risk_reward": 1.0,
        "confidence": "HIGH", "timeframe": "15m", "reason": "test",
    }
    errs = validate_signal(sig)
    assert any("stop_loss" in e for e in errs)


def test_plan_validation():
    good = {"id": "p1", "type": "Buy Pullback", "action": "BUY", "condition": "if",
            "entry": 100.0, "stop_loss": 95.0, "take_profits": [110.0], "confidence": 80}
    assert validate_plan(good) == []
    bad = dict(good, action="BUY", stop_loss=105.0)
    assert any("stop_loss" in e for e in validate_plan(bad))


def test_validate_output_full(df):
    out = analyze_frame(df, min_confidence=0)
    payload = out.as_json()
    res = validate_output(payload)
    assert res["ok"] is True, res["errors"]


def test_output_has_plans_and_snapshot(df):
    out = analyze_frame(df, min_confidence=0)
    payload = out.as_json()
    assert "plans" in payload and "snapshot" in payload
    assert "features" in payload["snapshot"]
    assert "scores" in payload["snapshot"]


def test_regime_tagged_features(df):
    """Decision B3: every frame is regime-tagged."""
    out = analyze_frame(df, symbol="BTCUSDT", timeframe="15m", min_confidence=0)
    f = out.features
    assert "regime_name" in f and f["regime_name"]
    assert "regime_label" in f and "regime" in f
    assert f["regime"]["regime"] == f["regime_name"]


def test_primary_narrowing(df):
    """Decision A1: outside the family, plans are watch-items and cannot
    become the best signal."""
    out_all = analyze_frame(df, symbol="BTCUSDT", timeframe="15m", min_confidence=0)
    out_narrow = analyze_frame(df, symbol="BTCUSDT", timeframe="15m", min_confidence=0,
                               primary_types={"Buy Pullback", "Sell Pullback"})
    assert any(p["primary"] for p in out_all.plans)
    for p in out_narrow.plans:
        assert p["primary"] == (p["type"] in {"Buy Pullback", "Sell Pullback"})
    # when narrowing is active the best signal always comes from a primary plan
    best = out_narrow.best_signal
    if best["action"] in ("BUY", "SELL") and best.get("entry"):
        primaries = [p for p in out_narrow.plans if p["primary"]]
        assert any(abs(float(p.get("entry") or 0) - float(best["entry"])) < 1e-6
                   for p in primaries)


def test_tp_rr_by_type(df):
    """Decision A2: per-setup TP distance in R from measured expectancy."""
    out = analyze_frame(df, symbol="BTCUSDT", timeframe="15m", min_confidence=0,
                        tp_rr_by_type={"Buy Pullback": 3.0})
    for p in out.plans:
        if p["type"] == "Buy Pullback" and p.get("entry") and p.get("stop_loss"):
            risk = abs(p["entry"] - p["stop_loss"])
            tp1 = p["take_profits"][0]
            assert tp1 > p["entry"]  # BUY
            assert round((tp1 - p["entry"]) / risk, 2) == 3.0
