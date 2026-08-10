"""Unit tests for brain/institutional_score.py — IPS, TP Ladder, Kelly Criterion, and Expiry."""
from __future__ import annotations

from brain.institutional_score import (
    compute_hold_time_and_expiry,
    compute_ips_score,
    compute_smart_tp_ladder,
    compute_entry_zone,
    compute_kelly_criterion,
)


def test_hold_time_and_expiry():
    hold_15m, exp_15m, ts_15m = compute_hold_time_and_expiry("15m")
    assert hold_15m == "4–8 Hours"
    assert "UTC" in exp_15m
    assert ts_15m > 0

    hold_4h, exp_4h, _ = compute_hold_time_and_expiry("4h")
    assert hold_4h == "2–5 Days"


def test_compute_ips_score():
    features = {
        "event_kind": "bos_up",
        "sweep": {"side": "sellside", "level": 60000.0},
        "adx": 28.0,
        "supertrend_bull": True,
        "volume_ratio": 1.6,
        "obv_slope": 0.1,
    }
    mtf = {"alignment": {"score": 75}}
    ctx = {"macro": {"available": True}}
    plan = {"risk_reward": 3.2}
    regime = {"regime": "TRENDING_BULL", "trap_detected": False}

    ips, grade, breakdown = compute_ips_score(features, mtf, ctx, plan, regime, base_confidence=85)
    assert 0 <= ips <= 100
    assert ips >= 70
    assert grade in ("A+", "A", "B")
    assert "structure_smc" in breakdown
    assert "mtf_alignment" in breakdown


def test_smart_tp_ladder():
    entry = 60000.0
    sl = 59000.0
    atr = 400.0
    tps = compute_smart_tp_ladder("BUY", entry, sl, [], atr)
    assert len(tps) == 3
    assert tps[0]["target"] == "TP1"
    assert tps[0]["price"] > entry
    assert tps[0]["gain_pct"] > 0
    assert tps[0]["allocation_pct"] == 50
    assert tps[1]["target"] == "TP2"
    assert tps[2]["target"] == "TP3"


def test_compute_entry_zone():
    entry = 60000.0
    atr = 500.0
    low, high, fmt_str = compute_entry_zone(entry, atr, "BUY")
    assert low < entry < high
    assert "$" in fmt_str


def test_kelly_criterion():
    res = compute_kelly_criterion(win_rate_pct=65.0, risk_reward=2.5, account_balance=10000.0, max_risk_cap=2.0)
    assert res["win_rate_assumed_pct"] == 65.0
    assert res["full_kelly_pct"] > 0
    assert 0 < res["recommended_risk_pct"] <= 2.0
    assert res["recommended_risk_amt"] > 0
    assert "Isolated" in res["recommended_leverage"] or "Spot" in res["recommended_leverage"]
