"""Offline smoke test verifying signal engine on sample data."""
from __future__ import annotations

import pandas as pd
from engine.signal_engine import analyze_frame


def test_smoke_engine_sample():
    df = pd.read_csv("data_samples/btcusdt_15m_sample.csv")
    out = analyze_frame(df, symbol="BTCUSDT", timeframe="15m")
    assert out.best_signal["asset"] == "BTCUSDT"
    assert out.best_signal["action"] in ("BUY", "SELL", "NO TRADE")
    assert out.best_signal["confidence"] in ("HIGH", "MEDIUM", "LOW", "NO TRADE")
