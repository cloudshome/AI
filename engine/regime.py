"""engine/regime.py — Market Regime Classification & Liquidity Trap Detection.

Identifies market environment (Trending Bull/Bear, Ranging, Volatility Compression/Expansion,
Liquidity Trap / Chop) and filters fake breakouts (Swing Failure Patterns, volume divergence).
"""
from __future__ import annotations

from typing import Optional
import numpy as np
import pandas as pd


def classify_market_regime(df: pd.DataFrame, features: dict) -> dict:
    """Classify the current market regime and detect manipulation / fakeout traps.

    Returns:
      regime: 'TRENDING_BULL' | 'TRENDING_BEAR' | 'RANGING' | 'VOLATILITY_EXPANSION' |
              'VOLATILITY_COMPRESSION' | 'LIQUIDITY_TRAP_CHOP'
      label: Human-readable description
      trap_detected: bool
      fake_breakout: bool
      liquidity_void: bool
      volatility_state: 'HIGH' | 'NORMAL' | 'COMPRESSED'
      details: dict with supporting metrics
    """
    if df is None or len(df) < 20:
        return {
            "regime": "RANGING",
            "label": "Ranging / Neutral (Insufficient data)",
            "trap_detected": False,
            "fake_breakout": False,
            "liquidity_void": False,
            "volatility_state": "NORMAL",
            "details": {},
        }

    adx = float(features.get("adx") or 0.0)
    trend = str(features.get("trend") or "mixed")
    bb_compress = bool(features.get("bb_compress"))
    atr_pct = float(features.get("atr_pct") or 1.0)
    vol_ratio = float(features.get("volume_ratio") or 1.0)
    event_kind = str(features.get("event_kind") or "")

    # Calculate recent wick-to-body ratios for trap detection
    tail = df.tail(15).copy()
    highs = tail["high"].astype(float).values
    lows = tail["low"].astype(float).values
    opens = tail["open"].astype(float).values
    closes = tail["close"].astype(float).values
    volumes = tail["volume"].astype(float).values if "volume" in tail else np.ones(len(tail))

    ranges = np.maximum(highs - lows, 1e-9)
    bodies = np.abs(closes - opens)
    upper_wicks = highs - np.maximum(opens, closes)
    lower_wicks = np.minimum(opens, closes) - lows
    wick_ratio = np.mean((upper_wicks + lower_wicks) / ranges)

    # 1. Fake Breakout / SFP (Swing Failure Pattern) detection
    swing_hi = features.get("swing_high")
    swing_lo = features.get("swing_low")
    last_c = closes[-1]
    last_h = highs[-1]
    last_l = lows[-1]
    last_v = volumes[-1]
    avg_v = np.mean(volumes[:-1]) if len(volumes) > 1 else last_v

    fake_breakout_up = False
    fake_breakout_down = False
    if swing_hi and last_h > swing_hi and last_c < swing_hi:
        # Wicked above swing high but closed back below it (Bearish SFP)
        fake_breakout_up = True
    if swing_lo and last_l < swing_lo and last_c > swing_lo:
        # Wicked below swing low but closed back above it (Bullish SFP)
        fake_breakout_down = True

    # Low-volume breakout warning
    volume_exhaustion = (last_h > np.max(highs[:-1]) or last_l < np.min(lows[:-1])) and (last_v < avg_v * 0.7)
    fake_breakout = fake_breakout_up or fake_breakout_down or volume_exhaustion

    # 2. Liquidity Void / Imbalance spike
    liquidity_void = False
    if len(df) >= 3:
        # Large displacement candle with minimal overlap
        prev_h = highs[-2]
        prev_l = lows[-2]
        if (last_c > prev_h and bodies[-1] > np.mean(bodies) * 2.2) or \
           (last_c < prev_l and bodies[-1] > np.mean(bodies) * 2.2):
            liquidity_void = True

    # 3. Volatility State
    if bb_compress or atr_pct < 0.6:
        volatility_state = "COMPRESSED"
    elif atr_pct > 2.2 or vol_ratio > 2.5:
        volatility_state = "HIGH"
    else:
        volatility_state = "NORMAL"

    # 4. Regime Determination
    trap_detected = fake_breakout or (wick_ratio > 0.65 and adx < 18)

    if bb_compress:
        regime = "VOLATILITY_COMPRESSION"
        label = "Volatility Compression (Squeeze Pre-Breakout)"
    elif trap_detected and adx < 22:
        regime = "LIQUIDITY_TRAP_CHOP"
        label = "Liquidity Trap / High-Wick Chop (Fakeout Risk)"
    elif (volatility_state == "HIGH" and vol_ratio >= 1.8) or "bos" in event_kind:
        regime = "VOLATILITY_EXPANSION"
        label = "Volatility Expansion (Institutional Flow)"
    elif trend == "bullish" and adx >= 22:
        regime = "TRENDING_BULL"
        label = "Trending Bullish (Strong Trend)"
    elif trend == "bearish" and adx >= 22:
        regime = "TRENDING_BEAR"
        label = "Trending Bearish (Strong Trend)"
    elif trend == "bullish":
        regime = "TRENDING_BULL"
        label = "Moderate Bullish Trend"
    elif trend == "bearish":
        regime = "TRENDING_BEAR"
        label = "Moderate Bearish Trend"
    else:
        regime = "RANGING"
        label = "Ranging / Consolidation Zone"

    return {
        "regime": regime,
        "label": label,
        "trap_detected": trap_detected,
        "fake_breakout": fake_breakout,
        "fake_breakout_type": "Bearish SFP" if fake_breakout_up else "Bullish SFP" if fake_breakout_down else "Volume Divergence" if volume_exhaustion else "None",
        "liquidity_void": liquidity_void,
        "volatility_state": volatility_state,
        "wick_ratio": round(float(wick_ratio), 3),
        "details": {
            "adx": round(adx, 2),
            "atr_pct": round(atr_pct, 2),
            "volume_ratio": round(vol_ratio, 2),
            "bb_compress": bb_compress,
        },
    }
