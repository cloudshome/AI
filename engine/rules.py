"""engine/rules.py

The multi-condition plan generator. Instead of emitting one forced entry, the
engine produces a set of *conditional* trade plans — the way a discretionary
ICT/SMC trader thinks:

  plan: Immediate Buy          -> entry now if confluence is already strong
  plan: Buy Pullback at OB     -> IF price returns to the bullish order block
  plan: Breakout Buy           -> IF a 15m candle closes above the swing high
  plan: Reversal Sell          -> IF buyside liquidity is swept + bearish CHOCH
  plan: FVG Retest             -> IF price retraces into an unfilled fair value gap

Every plan carries: human-readable condition, trigger level, entry, stop-loss,
take-profit ladder, risk:reward, confidence %, and the reasons it was created.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .scorer import ScoreBreakdown, _mapped_confidence


@dataclass
class Plan:
    id: str
    type: str
    action: str                      # BUY | SELL
    condition: str                   # human-readable IF statement
    trigger_level: Optional[float]   # level that must be reached / broken
    entry: Optional[float]
    stop_loss: Optional[float]
    take_profits: list = field(default_factory=list)
    risk_reward: float = 0.0
    confidence_pct: int = 0
    confidence_label: str = "LOW"
    reasons: list = field(default_factory=list)
    status: str = "active"           # active | waiting
    primary: bool = True             # True = eligible to become the best signal

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "action": self.action,
            "condition": self.condition,
            "trigger_level": round(self.trigger_level, 2) if self.trigger_level else None,
            "entry": round(self.entry, 2) if self.entry else None,
            "stop_loss": round(self.stop_loss, 2) if self.stop_loss else None,
            "take_profits": [round(tp, 2) for tp in self.take_profits],
            "risk_reward": round(self.risk_reward, 2),
            "confidence": self.confidence_pct,
            "confidence_label": self.confidence_label,
            "reasons": self.reasons,
            "status": self.status,
            "primary": self.primary,
        }


def _tp_rr_for(plan_type: str, default_rr: float,
               tp_rr_by_type: Optional[dict]) -> float:
    """Per-setup take-profit distance in R (decision A2). Falls back to the
    engine default when no measured profile exists yet."""
    if tp_rr_by_type and plan_type in tp_rr_by_type:
        try:
            v = float(tp_rr_by_type[plan_type])
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return default_rr


def _rr(entry: float, sl: float, tps: list[float]) -> float:
    risk = abs(entry - sl)
    if risk <= 0 or not tps:
        return 0.0
    reward = sum(abs(tp - entry) for tp in tps[:2]) / len(tps[:2])
    return round(reward / risk, 2)


def _confidence(score: ScoreBreakdown) -> tuple[int, str]:
    return score.confidence_pct, score.confidence


def _sl_buffer(price: float, atr: float, side: str, min_pct: float = 0.3) -> float:
    """Structural stop: at least ATR * 1.5 but never less than min_pct% away."""
    raw = max(atr * 1.5, price * min_pct / 100)
    if side == "BUY":
        return round(price - raw, 2)
    return round(price + raw, 2)


def build_plans(f: dict, bull: ScoreBreakdown, bear: ScoreBreakdown,
                min_confidence: int = 55, default_rr: float = 2.0,
                max_plans: int = 8, calibration: dict | None = None,
                primary_types: set | None = None,
                tp_rr_by_type: dict | None = None,
                regime: str = "") -> list[Plan]:
    price = f["price"]
    atr = f.get("atr") or price * 0.003
    plans: list[Plan] = []

    def _is_primary(plan_type: str) -> bool:
        """Decision A1: outside the chosen primary setup family, plans are
        research watch-items and can never become the best signal."""
        if primary_types is None:
            return True
        return plan_type in primary_types

    # ── 1. Immediate entries (strong confluence right now) ───────────────
    if bull.confidence_pct >= min_confidence:
        rr = _tp_rr_for("Immediate Buy", default_rr, tp_rr_by_type)
        sl = _sl_buffer(price, atr, "BUY")
        tps = [round(price + (price - sl) * rr, 2),
               round(price + (price - sl) * rr * 1.5, 2)]
        plans.append(Plan(
            id="imm_buy", type="Immediate Buy", action="BUY",
            condition=f"Enter now — {bull.confidence_pct}% confluence at {price:,.2f}",
            trigger_level=None, entry=price, stop_loss=sl, take_profits=tps,
            risk_reward=_rr(price, sl, tps), confidence_pct=bull.confidence_pct,
            confidence_label=bull.confidence, reasons=bull.fired,
            primary=_is_primary("Immediate Buy"),
        ))
    if bear.confidence_pct >= min_confidence:
        rr = _tp_rr_for("Immediate Sell", default_rr, tp_rr_by_type)
        sl = _sl_buffer(price, atr, "SELL")
        tps = [round(price - (sl - price) * rr, 2),
               round(price - (sl - price) * rr * 1.5, 2)]
        plans.append(Plan(
            id="imm_sell", type="Immediate Sell", action="SELL",
            condition=f"Enter now — {bear.confidence_pct}% confluence at {price:,.2f}",
            trigger_level=None, entry=price, stop_loss=sl, take_profits=tps,
            risk_reward=_rr(price, sl, tps), confidence_pct=bear.confidence_pct,
            confidence_label=bear.confidence, reasons=bear.fired,
            primary=_is_primary("Immediate Sell"),
        ))

    # ── 2. Buy pullback into bullish OB / FVG / discount zone ────────────
    ob_level = f.get("nearest_bull_ob")
    fvg_level = f.get("nearest_bull_fvg")
    pull_level = None
    if ob_level:
        pull_level = ob_level
        source = "bullish Order Block"
    elif fvg_level:
        pull_level = fvg_level
        source = "unfilled bullish FVG"
    elif f.get("premium_discount") == "discount" and f.get("swing_low"):
        pull_level = max(f["swing_low"], price * 0.985)
        source = "discount zone / swing low"
    if pull_level and pull_level < price:
        rr = _tp_rr_for("Buy Pullback", default_rr, tp_rr_by_type)
        sl = _sl_buffer(pull_level, atr, "BUY")
        tps = [round(pull_level + (pull_level - sl) * rr, 2),
               round(price + (price - sl) * rr * 1.2, 2)]
        conf = min(95, bull.confidence_pct + 8)
        label, _ = _mapped_confidence(conf)
        plans.append(Plan(
            id="buy_pullback", type="Buy Pullback", action="BUY",
            condition=f"IF price pulls back to {source} near {pull_level:,.2f} AND shows rejection (bullish candle)",
            trigger_level=pull_level, entry=round(pull_level, 2), stop_loss=sl,
            take_profits=tps, risk_reward=_rr(pull_level, sl, tps),
            confidence_pct=conf, confidence_label=label,
            reasons=bull.fired + [f"Pullback target = {source}"],
            status="waiting", primary=_is_primary("Buy Pullback"),
        ))

    # ── 3. Sell pullback into bearish OB / FVG ───────────────────────────
    ob_s = f.get("nearest_bear_ob")
    fvg_s = f.get("nearest_bear_fvg")
    pull_s = None
    if ob_s:
        pull_s = ob_s
        source_s = "bearish Order Block"
    elif fvg_s:
        pull_s = fvg_s
        source_s = "unfilled bearish FVG"
    elif f.get("premium_discount") == "premium" and f.get("swing_high"):
        pull_s = min(f["swing_high"], price * 1.015)
        source_s = "premium zone / swing high"
    if pull_s and pull_s > price:
        rr = _tp_rr_for("Sell Pullback", default_rr, tp_rr_by_type)
        sl = _sl_buffer(pull_s, atr, "SELL")
        tps = [round(pull_s - (sl - pull_s) * rr, 2),
               round(price - (sl - price) * rr * 1.2, 2)]
        conf = min(95, bear.confidence_pct + 8)
        label, _ = _mapped_confidence(conf)
        plans.append(Plan(
            id="sell_pullback", type="Sell Pullback", action="SELL",
            condition=f"IF price rallies to {source_s} near {pull_s:,.2f} AND shows rejection (bearish candle)",
            trigger_level=pull_s, entry=round(pull_s, 2), stop_loss=sl,
            take_profits=tps, risk_reward=_rr(pull_s, sl, tps),
            confidence_pct=conf, confidence_label=label,
            reasons=bear.fired + [f"Pullback target = {source_s}"],
            status="waiting", primary=_is_primary("Sell Pullback"),
        ))

    # ── 4. Breakout buy above swing high / BOS level ─────────────────────
    swing_high = f.get("swing_high")
    if swing_high and swing_high > price and price > (swing_high * 0.985):
        rr = _tp_rr_for("Breakout Buy", default_rr, tp_rr_by_type)
        entry = round(swing_high * 1.0005, 2)
        sl = _sl_buffer(swing_high, atr, "BUY")
        tps = [round(entry + (entry - sl) * rr, 2),
               round(entry + (entry - sl) * rr * 1.5, 2)]
        conf = min(92, max(bull.confidence_pct + 5, 60))
        label, _ = _mapped_confidence(conf)
        plans.append(Plan(
            id="breakout_buy", type="Breakout Buy", action="BUY",
            condition=f"IF a candle CLOSES above swing high {swing_high:,.2f} (BOS confirmation) with volume",
            trigger_level=swing_high, entry=entry, stop_loss=sl,
            take_profits=tps, risk_reward=_rr(entry, sl, tps),
            confidence_pct=conf, confidence_label=label,
            reasons=bull.fired + [f"Breakout above {swing_high:,.2f}",
                                  "requires close + volume confirmation"],
            status="waiting", primary=_is_primary("Breakout Buy"),
        ))

    # ── 5. Reversal sell after buyside liquidity sweep ───────────────────
    sweep = f.get("sweep") or {}
    if sweep.get("side") == "buyside":
        rr = _tp_rr_for("Sweep Reversal Sell", default_rr, tp_rr_by_type)
        entry = price
        sl = _sl_buffer(price, atr, "SELL")
        tps = [round(price - (sl - price) * rr, 2),
               round(price - (sl - price) * rr * 1.5, 2)]
        conf = min(90, max(bear.confidence_pct + 10, 62))
        label, _ = _mapped_confidence(conf)
        plans.append(Plan(
            id="reversal_sell", type="Sweep Reversal Sell", action="SELL",
            condition=(f"IF buyside liquidity was swept at {sweep.get('level')} AND "
                       f"price shows bearish CHOCH / rejection"),
            trigger_level=sweep.get("level"), entry=entry, stop_loss=sl,
            take_profits=tps, risk_reward=_rr(entry, sl, tps),
            confidence_pct=conf, confidence_label=label,
            reasons=bear.fired + ["Buyside stop hunt detected"],
            primary=_is_primary("Sweep Reversal Sell"),
        ))
    elif sweep.get("side") == "sellside":
        rr = _tp_rr_for("Sweep Reversal Buy", default_rr, tp_rr_by_type)
        entry = price
        sl = _sl_buffer(price, atr, "BUY")
        tps = [round(price + (price - sl) * rr, 2),
               round(price + (price - sl) * rr * 1.5, 2)]
        conf = min(90, max(bull.confidence_pct + 10, 62))
        label, _ = _mapped_confidence(conf)
        plans.append(Plan(
            id="reversal_buy", type="Sweep Reversal Buy", action="BUY",
            condition=(f"IF sellside liquidity was swept at {sweep.get('level')} AND "
                       f"price shows bullish CHOCH / rejection"),
            trigger_level=sweep.get("level"), entry=entry, stop_loss=sl,
            take_profits=tps, risk_reward=_rr(entry, sl, tps),
            confidence_pct=conf, confidence_label=label,
            reasons=bull.fired + ["Sellside stop hunt detected"],
            primary=_is_primary("Sweep Reversal Buy"),
        ))

    # ── 6. FVG retest (unfilled gap in trade direction) ──────────────────
    if fvg_level and fvg_level < price and bull.confidence_pct >= 45:
        rr = _tp_rr_for("FVG Retest Buy", default_rr, tp_rr_by_type)
        sl = _sl_buffer(fvg_level, atr, "BUY")
        tps = [round(fvg_level + (fvg_level - sl) * rr, 2),
               round(price + (price - sl) * rr * 1.1, 2)]
        conf = min(88, bull.confidence_pct + 5)
        label, _ = _mapped_confidence(conf)
        plans.append(Plan(
            id="fvg_retest_buy", type="FVG Retest Buy", action="BUY",
            condition=f"IF price retraces into unfilled bullish FVG at {fvg_level:,.2f} and holds",
            trigger_level=fvg_level, entry=round(fvg_level, 2), stop_loss=sl,
            take_profits=tps, risk_reward=_rr(fvg_level, sl, tps),
            confidence_pct=conf, confidence_label=label,
            reasons=bull.fired + ["Unfilled bullish fair value gap"],
            status="waiting", primary=_is_primary("FVG Retest Buy"),
        ))
    if fvg_s and fvg_s > price and bear.confidence_pct >= 45:
        rr = _tp_rr_for("FVG Retest Sell", default_rr, tp_rr_by_type)
        sl = _sl_buffer(fvg_s, atr, "SELL")
        tps = [round(fvg_s - (sl - fvg_s) * rr, 2),
               round(price - (sl - price) * rr * 1.1, 2)]
        conf = min(88, bear.confidence_pct + 5)
        label, _ = _mapped_confidence(conf)
        plans.append(Plan(
            id="fvg_retest_sell", type="FVG Retest Sell", action="SELL",
            condition=f"IF price rallies into unfilled bearish FVG at {fvg_s:,.2f} and rejects",
            trigger_level=fvg_s, entry=round(fvg_s, 2), stop_loss=sl,
            take_profits=tps, risk_reward=_rr(fvg_s, sl, tps),
            confidence_pct=conf, confidence_label=label,
            reasons=bear.fired + ["Unfilled bearish fair value gap"],
            status="waiting", primary=_is_primary("FVG Retest Sell"),
        ))

    # Apply the calibration profile (self-improvement) before filtering:
    # boost positive-expectancy plan types, dampen negative ones, drop filtered.
    # Profiles are keyed by plan_type or plan_type::regime (decision B3).
    if calibration:
        from .calibration_hook import apply_calibration
        kept: list[Plan] = []
        for p in plans:
            conf, filtered = apply_calibration(p.confidence_pct, p.type, calibration,
                                               regime=regime)
            if filtered:
                continue
            if conf != p.confidence_pct:
                label, _ = _mapped_confidence(conf)
                p.confidence_pct = conf
                p.confidence_label = label
            kept.append(p)
        plans = kept

    # Filter to the trade threshold, sort by confidence, cap the count.
    plans = [p for p in plans if p.confidence_pct >= min_confidence]
    plans.sort(key=lambda p: p.confidence_pct, reverse=True)
    return plans[:max_plans]
