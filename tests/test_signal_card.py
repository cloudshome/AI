"""Unit tests for output/signal_card.py — Terminal Box-Drawing & JSON representation."""
from __future__ import annotations

import pandas as pd
from engine.signal_engine import analyze_frame
from brain.trading_intelligence import build_intelligence
from output.signal_card import format_signal_card


def test_format_signal_card(df):
    out = analyze_frame(df, symbol="BTCUSDT", timeframe="15m", min_confidence=0)
    payload = out.as_json()
    payload["mtf"] = {"alignment": {"score": -50, "label": "bearish"}, "htf_bias": "bearish"}
    payload["context"] = {"macro": {"available": True, "high_impact_imminent": False}}

    intel = build_intelligence(payload, df=df)
    card_str = format_signal_card(intel)

    assert "INSTITUTIONAL AI TRADING PLATFORM v2.0" in card_str
    assert "BTCUSDT" in card_str
    assert "AI Confidence Index" in card_str
    assert "Institutional Probability" in card_str
    assert "Invalidation Conditions:" in card_str
    assert "Why AI Took This Trade:" in card_str
    assert "Alternative Scenario:" in card_str
