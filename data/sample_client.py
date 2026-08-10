"""data/sample_client.py — offline demo market-data client (DEMO_MODE=1).

Lets the dashboard, CLI and paper runner run end-to-end with NO network:

  * BTCUSDT 15m → the committed real sample (`data_samples/btcusdt_15m_sample.csv`)
  * everything else → deterministic synthetic OHLCV seeded by (symbol, timeframe)

This exists so the professional-mode flows (desk decision, playbooks,
portfolio veto, risk gate, paper runner) can be demonstrated and tested
anywhere.  Never used unless `DEMO_MODE=1` is set.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

SAMPLE = Path(__file__).resolve().parent.parent / "data_samples" / "btcusdt_15m_sample.csv"
BASE_PRICES = {"BTCUSDT": 60_000.0, "ETHUSDT": 3_000.0, "XAUUSD": 2_300.0,
               "ETHBTC": 0.05}
TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
         "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
         "1w": 604_800_000, "1M": 2_592_000_000}


def _seed(key: str) -> int:
    """Stable per-series seed.

    Plain ``hash()`` is salted per process in CPython, which made the demo
    series change between runs (and broke simulator dedupe).  crc32 is
    deterministic everywhere.
    """
    import zlib
    return zlib.crc32(key.encode("utf-8"))


def _synthetic(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    rng = np.random.default_rng(_seed(f"{symbol}:{timeframe}"))
    base = BASE_PRICES.get(symbol, 100.0)
    drift = 0.01 if "ETH" in symbol else 0.005
    n = max(limit, 60)
    close = np.zeros(n)
    close[0] = base
    for i in range(1, n):
        close[i] = max(base * 0.1, close[i - 1] + rng.normal(
            drift * close[i - 1] / 100, close[i - 1] * 0.004))
    high = close * (1 + np.abs(rng.normal(0, 0.0015, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.0015, n)))
    high = np.maximum(high, np.maximum(close, low) * 1.001)
    low = np.minimum(low, np.minimum(close, high) * 0.999)
    ts = np.arange(n, dtype=np.int64) * TF_MS.get(timeframe, 900_000) + 1_780_000_000_000
    return pd.DataFrame({
        "ts": ts, "open": np.round(close, 2), "high": np.round(high, 2),
        "low": np.round(low, 2), "close": np.round(close, 2),
        "volume": np.round(rng.uniform(50, 400, n), 2),
    })


class SampleClient:
    """Drop-in replacement for BinanceClient in DEMO_MODE."""

    def __init__(self, *args, **kwargs):
        pass

    def klines(self, symbol: str = "BTCUSDT", timeframe: str = "15m",
               limit: int = 500, start_time=None, end_time=None):
        if symbol == "BTCUSDT" and timeframe == "15m" and SAMPLE.exists():
            df = pd.read_csv(SAMPLE)
            df = df.tail(int(limit)).reset_index(drop=True)
            df.attrs["symbol"] = symbol
            df.attrs["timeframe"] = timeframe
            return df
        df = _synthetic(symbol, timeframe, int(limit))
        df.attrs["symbol"] = symbol
        df.attrs["timeframe"] = timeframe
        return df

    def market_context(self, symbol: str) -> dict:
        return {
            "data_symbol": symbol,
            "provider": "sample (DEMO_MODE)",
            "market": "crypto" if symbol != "XAUUSD" else "gold",
            "futures": False,
            "note": "DEMO_MODE=1 — synthetic/sample data, no live market",
            "funding_rate_pct": None, "open_interest": None,
            "long_short_ratio": None,
        }


def maybe_client():
    """Return the demo client when DEMO_MODE=1, else the real one."""
    if os.getenv("DEMO_MODE", "0") in ("1", "true", "yes"):
        return SampleClient()
    from data.binance_client import BinanceClient
    return BinanceClient()
