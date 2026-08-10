"""Unit tests for engine/regime.py — Market Regime Classification & Fakeout Detection."""
from __future__ import annotations

import pandas as pd
import numpy as np

from engine.regime import classify_market_regime


def test_regime_insufficient_data():
    df = pd.DataFrame({"close": [10, 11, 12]})
    res = classify_market_regime(df, {})
    assert res["regime"] == "RANGING"
    assert res["volatility_state"] == "NORMAL"


def test_regime_trending_bull(df):
    features = {
        "adx": 30.0,
        "trend": "bullish",
        "bb_compress": False,
        "atr_pct": 1.2,
        "volume_ratio": 1.4,
        "event_kind": "bos_up",
    }
    res = classify_market_regime(df, features)
    assert res["regime"] in ("TRENDING_BULL", "VOLATILITY_EXPANSION")
    assert "Trending" in res["label"] or "Expansion" in res["label"]


def test_regime_compression(df):
    features = {
        "adx": 14.0,
        "trend": "mixed",
        "bb_compress": True,
        "atr_pct": 0.5,
        "volume_ratio": 0.8,
    }
    res = classify_market_regime(df, features)
    assert res["regime"] == "VOLATILITY_COMPRESSION"
    assert res["volatility_state"] == "COMPRESSED"


def test_fake_breakout_sfp(df):
    # Test bearish SFP (wicked above swing high but closed below)
    features = {
        "adx": 15.0,
        "trend": "mixed",
        "swing_high": 63500.0,
        "swing_low": 63000.0,
    }
    # Modify last candle to wick above swing high and close below
    mod_df = df.copy()
    mod_df.loc[mod_df.index[-1], "high"] = 63600.0
    mod_df.loc[mod_df.index[-1], "close"] = 63400.0
    mod_df.loc[mod_df.index[-1], "open"] = 63350.0

    res = classify_market_regime(mod_df, features)
    assert res["fake_breakout"] is True
    assert res["fake_breakout_type"] == "Bearish SFP"
