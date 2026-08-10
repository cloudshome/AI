"""brain/institutional_score.py — Institutional Probability Score (IPS) & Trade Grading.

Computes fund-grade risk scores, trade quality grades (A+, A, B, C, F), Kelly Criterion
position sizing, smart TP ladders with percentage gains, entry zones, and active-until timestamps.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Any


def compute_hold_time_and_expiry(timeframe: str) -> tuple[str, str, int]:
    """Returns (expected_hold_time, active_until_utc_str, expiry_epoch_ms)."""
    tf = timeframe.lower().strip()
    now = datetime.now(timezone.utc)
    if tf in ("1m", "3m"):
        hold_str = "15–45 Minutes"
        expiry = now + timedelta(minutes=45)
    elif tf in ("5m",):
        hold_str = "1–3 Hours"
        expiry = now + timedelta(hours=3)
    elif tf in ("15m",):
        hold_str = "4–8 Hours"
        expiry = now + timedelta(hours=8)
    elif tf in ("30m",):
        hold_str = "6–16 Hours"
        expiry = now + timedelta(hours=16)
    elif tf in ("1h", "2h"):
        hold_str = "12–36 Hours"
        expiry = now + timedelta(hours=36)
    elif tf in ("4h", "6h", "8h", "12h"):
        hold_str = "2–5 Days"
        expiry = now + timedelta(days=5)
    elif tf in ("1d", "d", "daily", "1w", "weekly", "1m", "monthly") and tf not in ("1m",):
        hold_str = "1–3 Weeks"
        expiry = now + timedelta(days=21)
    else:
        hold_str = "4–8 Hours"
        expiry = now + timedelta(hours=8)

    expiry_str = expiry.strftime("%Y-%m-%d %H:%M UTC")
    expiry_ms = int(expiry.timestamp() * 1000)
    return hold_str, expiry_str, expiry_ms


def compute_ips_score(features: dict, mtf: dict, ctx: dict, plan: Optional[dict],
                      regime: dict, base_confidence: int) -> tuple[int, str, dict]:
    """Compute Institutional Probability Score (IPS 0–100) and Trade Quality Grade.

    Scoring Breakdown (Max 100):
      • Market Structure & SMC Quality: 25 pts (BOS/CHOCH + OB/FVG + Liquidity Sweep)
      • MTF Alignment & Trend Strength: 25 pts (Alignment score + ADX + Supertrend)
      • Volume & Order Flow Confirmation: 20 pts (Volume ratio + Spike + OBV)
      • Regime & Volatility Suitability: 15 pts (No trap + clear regime + healthy ATR)
      • Risk-Reward & Execution Geometry: 15 pts (R:R >= 2.5: +15, R:R >= 2.0: +10)
    """
    pts_smc = 0
    pts_mtf = 0
    pts_vol = 0
    pts_regime = 0
    pts_rr = 0

    # 1. SMC & Structure (25 pts)
    event = features.get("event_kind")
    if event in ("bos_up", "bos_down"):
        pts_smc += 10
    elif event in ("choch_up", "choch_down"):
        pts_smc += 8
    else:
        pts_smc += 4

    sweep = features.get("sweep") or {}
    if sweep.get("side"):
        pts_smc += 8
    elif features.get("liquidity_above") or features.get("liquidity_below"):
        pts_smc += 4

    if features.get("nearest_bull_ob") or features.get("nearest_bear_ob"):
        pts_smc += 4
    if (features.get("fvg_bull_count") or 0) + (features.get("fvg_bear_count") or 0) > 0:
        pts_smc += 3

    # 2. MTF & Trend (25 pts)
    align_score = abs(float((mtf.get("alignment") or {}).get("score", 0)))
    pts_mtf += min(15, int((align_score / 100.0) * 15))

    adx = float(features.get("adx") or 0.0)
    if adx >= 25:
        pts_mtf += 6
    elif adx >= 18:
        pts_mtf += 3

    if features.get("supertrend_bull") or features.get("supertrend_bear"):
        pts_mtf += 4

    # 3. Volume & Flow (20 pts)
    vol_ratio = float(features.get("volume_ratio") or 1.0)
    if vol_ratio >= 1.8 or features.get("volume_spike"):
        pts_vol += 12
    elif vol_ratio >= 1.2 or features.get("volume_above_avg"):
        pts_vol += 8
    else:
        pts_vol += 4

    obv_slope = float(features.get("obv_slope") or 0.0)
    if abs(obv_slope) > 0.05:
        pts_vol += 8
    else:
        pts_vol += 4

    # 4. Regime & Volatility (15 pts)
    if regime.get("trap_detected") or regime.get("fake_breakout"):
        pts_regime += 2
    elif regime.get("regime") in ("TRENDING_BULL", "TRENDING_BEAR", "VOLATILITY_EXPANSION"):
        pts_regime += 15
    elif regime.get("regime") == "VOLATILITY_COMPRESSION":
        pts_regime += 10
    else:
        pts_regime += 8

    # 5. Risk-Reward & Geometry (15 pts)
    rr = float((plan or {}).get("risk_reward") or 0.0)
    if rr >= 3.5:
        pts_rr += 15
    elif rr >= 2.5:
        pts_rr += 12
    elif rr >= 2.0:
        pts_rr += 9
    elif rr >= 1.5:
        pts_rr += 5

    raw_ips = pts_smc + pts_mtf + pts_vol + pts_regime + pts_rr
    raw_ips = min(100, max(0, raw_ips))

    # Blend with base confidence if available
    if base_confidence > 0:
        final_ips = int(round(raw_ips * 0.65 + base_confidence * 0.35))
    else:
        final_ips = raw_ips

    # Quality Grade
    if final_ips >= 90 and rr >= 2.8 and not regime.get("trap_detected"):
        grade = "A+"
    elif final_ips >= 80 and rr >= 2.2:
        grade = "A"
    elif final_ips >= 70 and rr >= 1.8:
        grade = "B"
    elif final_ips >= 60:
        grade = "C"
    else:
        grade = "F"

    breakdown = {
        "structure_smc": min(25, pts_smc),
        "mtf_alignment": min(25, pts_mtf),
        "volume_flow": min(20, pts_vol),
        "regime_volatility": min(15, pts_regime),
        "risk_reward_edge": min(15, pts_rr),
    }

    return final_ips, grade, breakdown


def compute_smart_tp_ladder(action: str, entry: float, stop_loss: float,
                            existing_tps: list[float], atr: float) -> list[dict]:
    """Compute TP1, TP2, TP3 targets with price, percent gain, and partial sizing recommendation."""
    if not entry or not stop_loss or entry <= 0:
        return []

    risk_dist = abs(entry - stop_loss)
    if risk_dist <= 0:
        risk_dist = max(atr * 1.5, entry * 0.005)

    is_buy = (action == "BUY")
    tps_out: list[dict] = []

    # Target multipliers: TP1 (1.5R - 2.0R), TP2 (2.5R - 3.2R), TP3 (4.0R - 5.0R)
    targets_r = [
        (1, 1.8, 50, "Move SL to Breakeven"),
        (2, 3.0, 30, "Lock in Profits"),
        (3, 4.5, 20, "Runner Target (Trailing ATR)"),
    ]

    for idx, r_mult, pct_close, note in targets_r:
        if len(existing_tps) >= idx and existing_tps[idx - 1]:
            target_price = float(existing_tps[idx - 1])
        else:
            if is_buy:
                target_price = entry + (risk_dist * r_mult)
            else:
                target_price = max(entry - (risk_dist * r_mult), 1e-6)

        pct_gain = ((target_price - entry) / entry * 100.0) if is_buy else ((entry - target_price) / entry * 100.0)
        tps_out.append({
            "target": f"TP{idx}",
            "price": round(target_price, 2) if target_price >= 1 else round(target_price, 6),
            "gain_pct": round(pct_gain, 2),
            "allocation_pct": pct_close,
            "management": note,
        })

    return tps_out


def compute_entry_zone(entry: float, atr: float, action: str) -> tuple[float, float, str]:
    """Compute entry zone (low and high bounds) around the target entry."""
    if not entry or entry <= 0:
        return (0.0, 0.0, "$0 – $0")
    buffer = max(atr * 0.20, entry * 0.0012)
    low_bound = round(entry - buffer, 2) if entry >= 1 else round(entry - buffer, 6)
    high_bound = round(entry + buffer, 2) if entry >= 1 else round(entry + buffer, 6)
    fmt_str = f"${low_bound:,.2f} – ${high_bound:,.2f}" if entry >= 10 else f"${low_bound} – ${high_bound}"
    return (low_bound, high_bound, fmt_str)


def compute_kelly_criterion(win_rate_pct: float, risk_reward: float,
                            account_balance: float = 10000.0, max_risk_cap: float = 2.0) -> dict:
    """Compute Kelly Criterion optimal position fraction and conservative Half-Kelly."""
    p = max(0.01, min(0.99, win_rate_pct / 100.0))
    q = 1.0 - p
    b = max(0.5, risk_reward)

    # Kelly formula: f* = (p*b - q) / b
    kelly_full = (p * b - q) / b
    kelly_full_pct = max(0.0, kelly_full * 100.0)
    half_kelly_pct = kelly_full_pct / 2.0

    # Cap by max risk policy
    recommended_risk_pct = min(half_kelly_pct if half_kelly_pct > 0 else 1.0, max_risk_cap)
    recommended_risk_amt = round(account_balance * (recommended_risk_pct / 100.0), 2) if account_balance else 0.0

    # Recommended leverage
    if recommended_risk_pct >= 1.5:
        leverage = "3x – 5x (Isolated)"
    elif recommended_risk_pct >= 0.8:
        leverage = "2x – 3x (Isolated)"
    else:
        leverage = "1x (Spot / Low Margin)"

    return {
        "win_rate_assumed_pct": round(win_rate_pct, 1),
        "risk_reward": round(risk_reward, 2),
        "full_kelly_pct": round(kelly_full_pct, 2),
        "half_kelly_pct": round(half_kelly_pct, 2),
        "recommended_risk_pct": round(recommended_risk_pct, 2),
        "recommended_risk_amt": recommended_risk_amt,
        "recommended_leverage": leverage,
    }
