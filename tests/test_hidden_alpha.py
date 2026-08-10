"""tests/test_hidden_alpha.py — Unit tests for Hidden Alpha, HMM regime, order flow imbalance, and Kelly sizing."""
import numpy as np
import pandas as pd
import pytest
from engine.hidden_alpha import (estimate_regime_probabilities,
                                 detect_order_flow_imbalance,
                                 bayesian_kelly_sizing,
                                 pattern_similarity_embedding)


@pytest.fixture
def sample_df():
    np.random.seed(42)
    n = 60
    close = 60000.0 + np.cumsum(np.random.randn(n) * 100)
    high = close + np.abs(np.random.randn(n) * 50)
    low = close - np.abs(np.random.randn(n) * 50)
    open_p = close + np.random.randn(n) * 20
    vol = np.random.uniform(50, 500, n)
    return pd.DataFrame({
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": vol,
    })


def test_regime_probabilities(sample_df):
    res = estimate_regime_probabilities(sample_df)
    assert "dominant_regime" in res
    assert "probabilities" in res
    probs = res["probabilities"]
    assert "bull_trend" in probs
    assert "bear_trend" in probs
    assert "ranging" in probs
    assert "expansion" in probs
    assert sum(probs.values()) == pytest.approx(1.0, abs=0.05)


def test_order_flow_imbalance(sample_df):
    res = detect_order_flow_imbalance(sample_df)
    assert "cumulative_delta" in res
    assert res["delta_bias"] in ("bullish", "bearish", "neutral")
    assert isinstance(res["absorption_detected"], bool)


def test_bayesian_kelly_sizing():
    # Positive edge: 60% win rate, 2:1 RR -> Kelly > 0
    k_pos = bayesian_kelly_sizing(win_rate=0.60, avg_rr=2.0, max_risk_cap=1.0, kelly_fraction=0.25)
    assert k_pos["edge_status"] == "POSITIVE_EDGE_OPTIMIZED"
    assert 0.1 <= k_pos["recommended_risk_pct"] <= 1.0

    # Negative edge: 30% win rate, 1:1 RR -> Kelly <= 0
    k_neg = bayesian_kelly_sizing(win_rate=0.30, avg_rr=1.0)
    assert k_neg["edge_status"] == "NEGATIVE_EDGE_STAND_ASIDE"
    assert k_neg["recommended_risk_pct"] == 0.0


def test_pattern_similarity_embedding(sample_df):
    res = pattern_similarity_embedding(sample_df, window=20)
    assert "feature_vector" in res
    assert len(res["feature_vector"]) == 5
    assert res["vector_norm"] > 0.0
    assert res["fingerprint"].startswith("V8[")
