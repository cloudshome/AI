"""Tests for the per-asset professional playbooks (decisions A1, B1, B8)."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from brain.playbooks import (get_playbook, primary_plan_types, apply_playbook,
                             session_status, previous_day_levels, gold_gates,
                             eth_gates)


def _d1_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "ts": [1_780_000_000_000, 1_780_086_400_000],
        "open": [2000.0, 2010.0],
        "high": [2015.0, 2030.0],
        "low": [1990.0, 2005.0],
        "close": [2010.0, 2025.0],
        "volume": [100.0, 120.0],
    })


def test_primary_family_narrowing():
    """Decision A1: only the chosen family may become the best signal."""
    types = primary_plan_types("BTCUSDT")
    assert types == {"Sweep Reversal Buy", "Sweep Reversal Sell",
                     "Buy Pullback", "Sell Pullback"}
    assert "Breakout Buy" not in types
    assert "FVG Retest Buy" not in types


def test_playbooks_are_per_asset():
    """Decision B1: BTC, ETH and GOLD get different playbooks."""
    btc = get_playbook("BTCUSDT")
    eth = get_playbook("ETHUSDT")
    gold = get_playbook("XAUUSD")
    assert btc["htf_stack"] == ["4h", "1h", "15m"]
    assert gold["htf_stack"] == ["1d", "4h", "1h", "15m"]
    assert gold["sessions"] == ["london", "newyork"]
    assert gold["pdh_pdl"] is True
    assert eth.get("gate_btc") is True
    assert btc.get("gate_btc") is not True


def test_gold_news_block():
    """Decision B8: gold goes no-entry around high-impact US data."""
    res = apply_playbook("XAUUSD", "BUY", now=datetime(2026, 8, 9, 14, 0,
                                                       tzinfo=timezone.utc),
                         news_imminent=True, df_1d=_d1_frame())
    assert res["blocked"] is True
    assert "us_data_countdown" in res["blocking_checks"]


def test_gold_session_warn():
    """Off-window session warns (mode=warn) but does not hard-block."""
    res = apply_playbook("XAUUSD", "BUY", now=datetime(2026, 8, 9, 3, 0,
                                                       tzinfo=timezone.utc),
                         news_imminent=False, df_1d=_d1_frame())
    assert res["blocked"] is False
    assert any(c["check"] == "session_window" for c in res["checks"])


def test_gold_session_block_mode():
    import brain.playbooks as pb
    old = pb.GOLD_SESSION_MODE
    pb.GOLD_SESSION_MODE = "block"
    try:
        res = apply_playbook("XAUUSD", "BUY", now=datetime(2026, 8, 9, 3, 0,
                                                           tzinfo=timezone.utc),
                             news_imminent=False)
        assert res["blocked"] is True
        assert "session_window" in res["blocking_checks"]
    finally:
        pb.GOLD_SESSION_MODE = old


def test_session_status():
    assert session_status("XAUUSD", 10)["session"] == "london"
    assert session_status("XAUUSD", 18)["session"] == "newyork"
    assert session_status("XAUUSD", 3)["session"] == "asia"
    assert session_status("XAUUSD", 3)["open"] is False
    assert session_status("BTCUSDT", 3)["open"] is True


def test_previous_day_levels():
    pd_ = previous_day_levels(_d1_frame())
    assert pd_["available"] is True
    assert pd_["pdh"] == 2015.0
    assert pd_["pdl"] == 1990.0
    assert previous_day_levels(_d1_frame().iloc[:1])["available"] is False


def test_eth_gate_blocks_long_against_bearish_btc():
    """Decision B1: BTC first, ETH second."""
    res = eth_gates(btc_bias="bear", eth_btc_slope=-0.2, action="BUY")
    assert res["blocked"] is True


def test_eth_gate_allows_long_with_relative_strength():
    res = eth_gates(btc_bias="bear", eth_btc_slope=+0.5, action="BUY")
    assert res["blocked"] is False


def test_eth_gate_blocks_short_against_bullish_btc():
    res = eth_gates(btc_bias="bull", eth_btc_slope=+0.2, action="SELL")
    assert res["blocked"] is True


def test_eth_gate_neutral_when_btc_mixed():
    res = eth_gates(btc_bias="mixed", action="BUY")
    assert res["blocked"] is False
