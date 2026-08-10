"""brain/portfolio.py — portfolio risk engine (decision B2).

Your rulebook rule #7: **BTC and ETH are correlated risk.**  Long BTC + long
ETH is NOT two independent trades.  This engine treats BTC/ETH as ONE
crypto-risk bucket:

  * same-direction duplicates inside the bucket veto new signals,
  * the bucket has a maximum number of concurrent positions,
  * the bucket has a maximum combined planned risk,
  * gold is tracked separately (its own bucket, per playbook B8).

The engine is advisory-hard: it VETOES (blocked=True) so the desk decision
cannot produce a trade that doubles correlated exposure.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from config import CRYPTO_BUCKET_MAX_TRADES, MAX_CRYPTO_EXPOSURE_RISK_PCT


@dataclass
class BucketState:
    symbol: str
    market: str = "crypto"
    open_trades: list = field(default_factory=list)
    max_trades: int = CRYPTO_BUCKET_MAX_TRADES
    max_risk_pct: float = MAX_CRYPTO_EXPOSURE_RISK_PCT
    planned_risk_pct: float = 0.0

    @property
    def n_open(self) -> int:
        return len(self.open_trades)

    @property
    def directions(self) -> list[str]:
        return [t.get("action", "").upper() for t in self.open_trades]


def _bucket_of(symbol: str) -> str:
    """Crypto bucket = BTC + ETH (one correlated risk bucket); gold = own."""
    if symbol == "XAUUSD":
        return "gold"
    return "crypto"


def bucket_state(db, symbol: str) -> BucketState:
    """Current open/planned exposure for the symbol's risk bucket."""
    exposure = db.open_exposure()
    bucket = _bucket_of(symbol)
    market = "gold" if bucket == "gold" else "crypto"
    mine = [t for t in exposure if _bucket_of(t["symbol"]) == bucket]
    return BucketState(
        symbol=symbol,
        market=market,
        open_trades=mine,
        planned_risk_pct=round(len(mine) * 0.5, 2),  # effective planned risk/trade
    )


def portfolio_veto(db, symbol: str, action: str,
                   planned_risk_pct: float = 0.5) -> dict:
    """Decide whether a new trade is allowed at the portfolio level.

    Returns ``{allowed, reasons, exposure}``.
    """
    symbol = symbol.upper()
    action = (action or "").upper()
    state = bucket_state(db, symbol)
    reasons: list[str] = []
    allowed = True

    # 1) Same-direction duplicate in the same bucket (correlated risk).
    same_dir = [t for t in state.open_trades if t["action"].upper() == action]
    if same_dir:
        allowed = False
        reasons.append(
            f"Portfolio veto: {action} already open in the {state.market} risk "
            f"bucket ({state.n_open} open) — correlated exposure, not a second edge")

    # 2) Bucket size cap.
    if allowed and state.n_open >= state.max_trades:
        allowed = False
        reasons.append(
            f"Portfolio veto: {state.market} bucket is full "
            f"({state.n_open}/{state.max_trades} trades)")

    # 3) Combined planned-risk cap.
    if allowed and state.planned_risk_pct + planned_risk_pct > state.max_risk_pct:
        allowed = False
        reasons.append(
            f"Portfolio veto: combined planned risk "
            f"{state.planned_risk_pct + planned_risk_pct:.2f}% would exceed the "
            f"{state.market} bucket cap of {state.max_risk_pct:.2f}%")

    return {
        "allowed": allowed,
        "reasons": reasons,
        "exposure": {
            "bucket": state.market,
            "n_open": state.n_open,
            "max_trades": state.max_trades,
            "directions": state.directions,
            "planned_risk_pct": state.planned_risk_pct,
            "max_risk_pct": state.max_risk_pct,
        },
    }
