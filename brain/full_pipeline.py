"""brain/full_pipeline.py — the complete "human trader" analysis.

Combines:
  1. multi-timeframe view      (HTF bias → LTF execution)
  2. full market context       (news, macro, geopolitics, cycle, social,
                                equities, fear&greed, dominance)
  3. single-frame signal engine (indicators + structure + plans)
  4. trading-style classification
  5. state memory              (stable signals, no 30s spam)

Returns one JSON-able dict that the CLI and dashboard both render.
"""
from __future__ import annotations

import time
from typing import Optional

from config import SYMBOL, TIMEFRAME, BARS, MIN_CONFIDENCE, DEFAULT_RISK_REWARD
from data.symbols import normalize_symbol
from data.binance_client import BinanceClient
from data.sample_client import maybe_client
from engine.mtf import analyze_mtf, analyze_timeframe
from engine.signal_engine import analyze_frame
from output.signal_schema import validate_output
import brain.context as context_mod
from .calibrator import apply_calibration as _cal_apply
from .calibrator import suggest_tp_rr_by_type
from .state_memory import SignalMemory
from .styles import classify_styles
from .trading_intelligence import build_intelligence
from .playbooks import primary_plan_types
from .decision import build_decision


def _load_calibration(db=None) -> dict:
    try:
        from ..data.database import SignalDB
        with SignalDB() as _db:
            return _db.load_calibration()
    except Exception:
        return {}


def _eth_context(client: BinanceClient) -> dict:
    """ETH playbook context: BTC 4H bias + ETH/BTC relative-strength slope.

    Cheap and best-effort — every failure degrades to neutral so the pipeline
    never breaks because a context feed is unreachable.
    """
    out = {"btc_bias": None, "eth_btc_slope": None}
    try:
        df_btc = client.klines("BTCUSDT", "4h", 120)
        view = analyze_timeframe(df_btc, "4h")
        out["btc_bias"] = view.get("trend")  # bull | bear | mixed
    except Exception:
        pass
    try:
        df_eb = client.klines("ETHBTC", "4h", 80)
        if df_eb is not None and len(df_eb) > 20:
            closes = df_eb["close"].astype(float).to_numpy()
            slope = (closes[-1] / closes[-21] - 1) * 100.0
            out["eth_btc_slope"] = round(float(slope), 3)
    except Exception:
        pass
    return out


def analyze_full(symbol: str = SYMBOL, timeframe: str = TIMEFRAME,
                 bars: int = BARS, client: Optional[BinanceClient] = None,
                 with_context: bool = True, with_memory: bool = True) -> dict:
    """Run the complete pipeline. Returns a dict with keys:
    signal, plans, snapshot, styles, mtf, context, memory, market_context,
    validation, analyzed_at."""
    symbol = normalize_symbol(symbol)
    client = client or maybe_client()
    t0 = time.time()

    # 0) Fetch the execution-timeframe data ONCE and reuse it for both the
    #    single-frame engine and the MTF view (no duplicate downloads).
    df = client.klines(symbol, timeframe, bars)

    # 1) Multi-timeframe (parallel; skips re-fetching the execution TF)
    mtf = analyze_mtf(symbol, client, prefetched={timeframe: df})

    # 2) single-frame engine (with calibration + professional narrowing)
    calib = _load_calibration()
    primary = primary_plan_types(symbol)          # decision A1
    tp_rr = suggest_tp_rr_by_type(calib)          # decision A2 (data-driven TP)
    frame = analyze_frame(df, symbol=symbol, timeframe=timeframe,
                          min_confidence=MIN_CONFIDENCE,
                          default_rr=DEFAULT_RISK_REWARD, calibration=calib,
                          primary_types=primary, tp_rr_by_type=tp_rr)
    payload = frame.as_json()
    payload["market_context"] = client.market_context(symbol)

    # 3) context (cached, best-effort) — pass 1d SMA/price for the cycle view
    ctx = {}
    if with_context:
        v1d = mtf.get("views", {}).get("1d", {})
        ctx = context_mod.collect(price_1d=v1d.get("price"),
                                  sma200_1d=None if not v1d.get("available") else
                                  _sma200(v1d))
    payload["context"] = ctx
    payload["mtf"] = mtf

    # 4) styles
    styles = classify_styles(mtf, ctx, payload)
    payload["styles"] = styles

    # 5) state memory
    memory = {}
    if with_memory:
        mem = SignalMemory()
        mem_result = mem.update(symbol, timeframe, mtf, styles, payload)
        payload["memory"] = mem_result
        payload["memory_events"] = mem.history(symbol, timeframe, limit=12)
        memory = mem_result

    # 6) professional desk-style intelligence report (strict filters:
    #    confidence >=80, RR >= floor, no major conflict/news risk)
    payload["intelligence"] = build_intelligence(payload, df=df)

    # 7) FINAL professional decision (decision A4): desk filter + per-asset
    #    playbook (B1/B8) + portfolio/correlation veto (B2) + enforced risk &
    #    discipline gate (B6/B7/B9/B10).  This is the only output you act on.
    extra = {}
    if symbol == "ETHUSDT":
        extra = _eth_context(client)
    df_1d = None
    if symbol == "XAUUSD":
        try:
            df_1d = client.klines(symbol, "1d", 30)
        except Exception:
            df_1d = None
    payload["decision"] = build_decision(payload, symbol, btc_bias=extra.get("btc_bias"),
                                         eth_btc_slope=extra.get("eth_btc_slope"),
                                         df_1d=df_1d)

    payload["validation"] = validate_output(payload)
    payload["analyzed_at"] = int(time.time() * 1000)
    payload["elapsed_ms"] = int((time.time() - t0) * 1000)
    return payload


def _sma200(v: dict) -> Optional[float]:
    """Approx 200d SMA proxy from the 1d view (fall back to price)."""
    return v.get("price")


def summarize_styles(styles: dict) -> str:
    if not styles:
        return ""
    offered = styles.get("market_offering", [])
    if not offered:
        return "Stand aside — " + "; ".join(styles.get("stand_aside", [])) + "."
    return "Market offering: " + ", ".join(
        f"{s} ({styles['styles'][s]['direction']} {styles['styles'][s]['confidence']}%)"
        for s in offered) + "."
