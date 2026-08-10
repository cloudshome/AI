"""Live-market paper-trading runner.

This module deliberately **never** talks to an exchange trading endpoint and
never needs exchange credentials.  It watches public OHLCV candles for the
signal a human already approved, simulates the planned fill, then closes the
paper position when its stop-loss or first take-profit is reached.

Lifecycle integration
---------------------

    PENDING_REVIEW --human approves--> APPROVED
        --paper entry is reached--> EXECUTED
        --paper SL / TP is reached--> CLOSED

An immediate plan is paper-filled at its planned entry when the runner first
picks up the approval.  A conditional plan remains ``WAITING_ENTRY`` until a
live candle trades through its entry level.  The SQLite ``paper_trades`` table
keeps the durable audit record; its decided outcomes are also included by the
calibrator so live paper decisions can improve future setup scoring.

OHLCV only gives a candle's high and low, not tick-by-tick order.  If both the
stop and target appear in the same candle, the runner uses the conservative
rule: **stop first**.  This avoids overstating paper performance.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import pandas as pd

from config import PAPER_MAX_CANDLES_PER_CHECK
from data.symbols import normalize_symbol
from data.binance_client import TIMEFRAME_TO_MS
from data.database import SignalDB
from engine.lifecycle import LifecycleError


PAPER_WAITING = "WAITING_ENTRY"
PAPER_OPEN = "OPEN"
PAPER_CLOSED = "CLOSED"
PAPER_CANCELLED = "CANCELLED"

OUTCOME_TARGET = "TP_HIT"
OUTCOME_STOP = "STOP_LOSS"


class PaperTradeError(ValueError):
    """Raised when a scan does not contain a safe, monitorable paper plan."""


def _as_float(value: Any, name: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise PaperTradeError(f"paper trade has no valid {name}") from exc
    if out <= 0:
        raise PaperTradeError(f"paper trade {name} must be positive")
    return out


def validate_levels(action: str, entry: Any, stop_loss: Any, take_profit: Any) -> tuple[float, float, float]:
    """Validate trade direction and return normalised numeric levels."""
    side = (action or "").upper()
    entry_f = _as_float(entry, "entry")
    sl_f = _as_float(stop_loss, "stop loss")
    tp_f = _as_float(take_profit, "take profit")
    if side == "BUY" and not (sl_f < entry_f < tp_f):
        raise PaperTradeError("BUY paper trade requires stop loss < entry < take profit")
    if side == "SELL" and not (tp_f < entry_f < sl_f):
        raise PaperTradeError("SELL paper trade requires take profit < entry < stop loss")
    if side not in ("BUY", "SELL"):
        raise PaperTradeError(f"unsupported paper trade action: {action}")
    return entry_f, sl_f, tp_f


def entry_hit(trade: dict, candle: dict) -> bool:
    """Whether a candle traded through the paper entry price."""
    entry = float(trade["entry"])
    return float(candle["low"]) <= entry <= float(candle["high"])


def exit_event(trade: dict, candle: dict) -> Optional[dict]:
    """Return the decisive SL/TP event for one candle, if any.

    A candle that reaches both levels is intrinsically ambiguous without tick
    data.  We mark it as ambiguous and choose the stop (conservative outcome).
    """
    side = (trade.get("action") or "").upper()
    entry, sl, tp = validate_levels(side, trade.get("entry"), trade.get("stop_loss"),
                                    trade.get("take_profit"))
    high, low = float(candle["high"]), float(candle["low"])
    if side == "BUY":
        hit_stop, hit_target = low <= sl, high >= tp
    else:
        hit_stop, hit_target = high >= sl, low <= tp

    if not hit_stop and not hit_target:
        return None

    risk = abs(entry - sl)
    if hit_stop and hit_target:
        return {
            "outcome": OUTCOME_STOP,
            "exit_price": sl,
            "rr_achieved": -1.0,
            "reason": "stop and target touched in one candle; conservative stop-first rule",
            "ambiguous": True,
        }
    if hit_stop:
        return {
            "outcome": OUTCOME_STOP,
            "exit_price": sl,
            "rr_achieved": -1.0,
            "reason": "stop loss hit",
            "ambiguous": False,
        }
    return {
        "outcome": OUTCOME_TARGET,
        "exit_price": tp,
        "rr_achieved": round(abs(tp - entry) / risk, 3) if risk else 0.0,
        "reason": "take-profit target hit",
        "ambiguous": False,
    }


def _primary_plan(scan: dict) -> dict:
    """Recover the exact top plan that produced a persisted best signal.

    Old rows can pre-date plan storage or contain hand-edited JSON, so this is
    intentionally defensive and falls back to the scan-level signal fields.
    """
    try:
        plans = json.loads(scan.get("plans_json") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        plans = []
    plans = [p for p in plans if isinstance(p, dict)]
    side = (scan.get("action") or "").upper()
    entry = scan.get("entry")
    sl = scan.get("stop_loss")
    tp = scan.get("take_profit")

    # build_best_signal() uses plans[0].  Prefer a matching plan in case a
    # historical payload was reordered before it reached SQLite.
    matching = [p for p in plans if (p.get("action") or "").upper() == side]
    for p in matching:
        try:
            if (abs(float(p.get("entry")) - float(entry)) < 1e-8 and
                    abs(float(p.get("stop_loss")) - float(sl)) < 1e-8):
                return p
        except (TypeError, ValueError):
            continue
    if matching:
        return matching[0]
    return {
        "id": "signal", "type": "Signal", "action": side,
        "entry": entry, "stop_loss": sl, "take_profits": [tp] if tp is not None else [],
        "risk_reward": scan.get("risk_reward"),
        "confidence": scan.get("confidence_pct"), "status": "active",
    }


def _scan_regime(scan: dict) -> str:
    """Recover the market-regime tag from a scan row (decision B3)."""
    try:
        features = json.loads(scan.get("features_json") or "{}")
        return features.get("regime_name", "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""


def paper_fields_from_scan(scan: dict, created_ts: Optional[int] = None) -> dict:
    """Turn one approved scan row into validated paper-trade fields."""
    plan = _primary_plan(scan)
    side = (scan.get("action") or plan.get("action") or "").upper()
    entry = plan.get("entry", scan.get("entry"))
    sl = plan.get("stop_loss", scan.get("stop_loss"))
    targets = plan.get("take_profits") or []
    tp = targets[0] if targets else scan.get("take_profit")
    entry, sl, tp = validate_levels(side, entry, sl, tp)
    # A human may already have marked a conditional setup EXECUTED before the
    # runner starts.  Honour that explicit fill rather than putting it back in
    # a waiting state.
    is_waiting = ((plan.get("status") or "active").lower() == "waiting" and
                  scan.get("status") != "EXECUTED")
    rr = plan.get("risk_reward", scan.get("risk_reward"))
    try:
        rr = float(rr) if rr is not None else round(abs(tp - entry) / abs(entry - sl), 3)
    except (TypeError, ValueError):
        rr = round(abs(tp - entry) / abs(entry - sl), 3)
    return {
        "scan_id": int(scan["id"]),
        "signal_id": scan.get("signal_id"),
        "plan_id": plan.get("id", "signal"),
        "plan_type": plan.get("type", "Signal"),
        "symbol": scan.get("symbol"),
        "timeframe": scan.get("timeframe"),
        "action": side,
        "entry": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "risk_reward": rr,
        "confidence_pct": plan.get("confidence", scan.get("confidence_pct")),
        "status": PAPER_WAITING if is_waiting else PAPER_OPEN,
        "created_ts": int(created_ts or scan.get("lifecycle_ts") or time.time() * 1000),
        "opened_ts": None if is_waiting else int(created_ts or scan.get("lifecycle_ts") or time.time() * 1000),
        "entry_price": None if is_waiting else entry,
        "regime": _scan_regime(scan),
    }


@dataclass
class PaperRun:
    """JSON-ready summary of one monitoring pass."""
    enrolled: int = 0
    checked: int = 0
    opened: int = 0
    closed: int = 0
    cancelled: int = 0
    events: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "enrolled": self.enrolled,
            "checked": self.checked,
            "opened": self.opened,
            "closed": self.closed,
            "cancelled": self.cancelled,
            "events": self.events,
            "errors": self.errors,
        }


class PaperTradingRunner:
    """Monitor approved CryptoBrain signals using public market candles only."""

    def __init__(self, db: SignalDB, client: Any,
                 clock_ms: Optional[Callable[[], int]] = None,
                 candle_limit: int = PAPER_MAX_CANDLES_PER_CHECK,
                 enforce_gate: bool = True):
        self.db = db
        self.client = client
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.candle_limit = max(10, min(int(candle_limit), 1000))
        self.enforce_gate = enforce_gate

    def _event(self, result: PaperRun, trade: dict, event: str, **extra: Any) -> None:
        result.events.append({
            "trade_id": trade.get("id"), "scan_id": trade.get("scan_id"),
            "symbol": trade.get("symbol"), "timeframe": trade.get("timeframe"),
            "action": trade.get("action"), "event": event, **extra,
        })

    def enroll_approved(self, symbol: Optional[str] = None, result: Optional[PaperRun] = None) -> PaperRun:
        """Create durable paper records for un-enrolled APPROVED/EXECUTED scans.

        Decision B6/B7/B9/B10: the risk & discipline gate is enforced here
        too — while it is closed (daily limit, drawdown ladder, trader-state
        flags, unproven setup at a strict progression level) no new simulated
        position is enrolled.
        """
        symbol = normalize_symbol(symbol) if symbol else None
        result = result or PaperRun()
        from brain.risk_gate import evaluate as gate_evaluate, gate_message
        for scan in self.db.paper_candidates(symbol):
            try:
                if self.enforce_gate:
                    _plan_type = _primary_plan(scan).get("type")
                    gate = gate_evaluate(self.db, symbol=scan.get("symbol"),
                                         plan_type=_plan_type or None,
                                         action=scan.get("action"))
                    if not gate["allowed"]:
                        result.events.append({
                            "scan_id": scan.get("id"), "symbol": scan.get("symbol"),
                            "event": "GATE_BLOCKED",
                            "reason": gate_message(gate),
                        })
                        continue
                fields = paper_fields_from_scan(scan, created_ts=scan.get("lifecycle_ts") or self.clock_ms())
                trade, created = self.db.create_paper_trade(fields)
                if not created:
                    continue
                result.enrolled += 1
                if trade["status"] == PAPER_OPEN:
                    # Immediate plan: approval is the paper-trading consent and
                    # acts as a simulated fill at its stated entry.
                    if scan.get("status") == "APPROVED" and not self._ensure_executed(scan["id"], trade):
                        # A concurrent manual Skip/Close won the race after
                        # candidate selection.  Preserve the audit row but do
                        # not claim a fill that was never lifecycle-valid.
                        self.db.cancel_paper_trade(int(trade["id"]), reason="scan no longer approved",
                                                   closed_ts=self.clock_ms())
                        self._event(result, trade, "CANCELLED", reason="scan no longer approved")
                        result.cancelled += 1
                        continue
                    self._event(result, trade, "ENTRY_OPENED", price=trade["entry_price"],
                                reason="immediate plan paper-filled at approved entry")
                    result.opened += 1
                else:
                    self._event(result, trade, "WAITING_ENTRY", price=trade["entry"],
                                reason="conditional plan is waiting for its entry level")
            except (PaperTradeError, LifecycleError, ValueError) as exc:
                result.errors.append({"scan_id": scan.get("id"), "symbol": scan.get("symbol"),
                                      "error": str(exc)})
        return result

    def _ensure_executed(self, scan_id: int, trade: dict) -> bool:
        """Advance APPROVED -> EXECUTED only when the paper fill exists."""
        scan = self.db.get_scan(scan_id)
        if not scan:
            return False
        if scan["status"] == "EXECUTED":
            return True
        if scan["status"] != "APPROVED":
            return False
        note = (f"paper runner entered {trade['action']} at {float(trade['entry_price']):,.8g} "
                f"({trade.get('plan_type') or 'Signal'})")
        try:
            self.db.update_status(scan_id, "EXECUTED", note=note, reviewer="paper_runner")
            return True
        except LifecycleError:
            return False

    def _cancel_if_manually_ended(self, trade: dict, result: PaperRun) -> bool:
        """Keep a paper record in sync if the human manually ends the scan."""
        scan = self.db.get_scan(int(trade["scan_id"]))
        if scan and scan.get("status") in ("APPROVED", "EXECUTED"):
            return False
        reason = (f"scan status is {scan.get('status')}" if scan else "source scan was removed")
        if self.db.cancel_paper_trade(int(trade["id"]), reason=reason, closed_ts=self.clock_ms()):
            self._event(result, trade, "CANCELLED", reason=reason)
            result.cancelled += 1
        return True

    def _fetch_candles(self, trade: dict) -> pd.DataFrame:
        """Fetch from the saved candle cursor, including its current candle.

        Including the cursor candle is intentional: it may still be forming,
        so a level not touched on the previous polling pass can be touched on
        the next one.  The exchange's ``startTime`` is optional for simple
        test clients, hence the compatibility fallback.
        """
        tf_ms = TIMEFRAME_TO_MS.get(trade.get("timeframe"), 900_000)
        cursor = trade.get("last_candle_ts")
        start = int(cursor) if cursor is not None else max(0, int(trade["created_ts"]) - tf_ms)
        try:
            df = self.client.klines(trade["symbol"], trade["timeframe"],
                                    limit=self.candle_limit, start_time=start)
        except TypeError:
            # Allows lightweight custom/test clients written against the old
            # three-argument BinanceClient API.
            df = self.client.klines(trade["symbol"], trade["timeframe"], self.candle_limit)
        if not isinstance(df, pd.DataFrame) or df.empty:
            return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
        required = {"ts", "open", "high", "low", "close"}
        if not required.issubset(df.columns):
            raise PaperTradeError("market-data response is missing OHLCV fields")
        out = df.copy()
        out["ts"] = out["ts"].astype("int64")
        for col in ("open", "high", "low", "close"):
            out[col] = out[col].astype(float)
        # A fallback client can return more history than requested.  Retain one
        # candle before the requested boundary so the approval-time candle is
        # still considered, never an arbitrary older history.
        return out[out["ts"] >= start - tf_ms].sort_values("ts").reset_index(drop=True)

    @staticmethod
    def _excursion(trade: dict, candle: dict) -> tuple[Optional[float], Optional[float]]:
        """(mae, mfe) in price units for one candle vs the paper entry.

        MAE/MFE are journal fields (decision B5): how far against / for the
        position did price travel while the trade was open?
        """
        entry_price = trade.get("entry_price")
        if entry_price is None:
            return None, None
        entry_price = float(entry_price)
        high, low = float(candle["high"]), float(candle["low"])
        if (trade.get("action") or "").upper() == "BUY":
            mae = max(0.0, entry_price - low)
            mfe = max(0.0, high - entry_price)
        else:
            mae = max(0.0, high - entry_price)
            mfe = max(0.0, entry_price - low)
        return round(mae, 6), round(mfe, 6)

    def _process_trade(self, trade: dict, result: PaperRun) -> None:
        if self._cancel_if_manually_ended(trade, result):
            return
        candles = self._fetch_candles(trade)
        if candles.empty:
            self.db.touch_paper_trade(int(trade["id"]), checked_ts=self.clock_ms())
            return

        result.checked += 1
        status = trade["status"]
        last_ts: Optional[int] = trade.get("last_candle_ts")
        last_price: Optional[float] = trade.get("last_price")
        worst: Optional[float] = trade.get("mae")
        best: Optional[float] = trade.get("mfe")

        for candle in candles.to_dict("records"):
            ts = int(candle["ts"])
            # Do not replay completed history before the initial approval.
            if trade.get("last_candle_ts") is None and ts < int(trade["created_ts"]) - TIMEFRAME_TO_MS.get(trade.get("timeframe"), 900_000):
                continue
            last_ts, last_price = ts, float(candle["close"])

            if status == PAPER_WAITING:
                if not entry_hit(trade, candle):
                    continue
                # The entry may have happened on this candle; move both the
                # paper state and lifecycle before evaluating its exit levels.
                filled = dict(trade)
                filled["entry_price"] = float(trade["entry"])
                if not self._ensure_executed(int(trade["scan_id"]), filled):
                    self.db.cancel_paper_trade(int(trade["id"]), reason="scan no longer approved", closed_ts=self.clock_ms())
                    self._event(result, trade, "CANCELLED", reason="scan no longer approved")
                    result.cancelled += 1
                    return
                if self.db.open_paper_trade(int(trade["id"]), entry_price=float(trade["entry"]),
                                            opened_ts=ts, last_candle_ts=ts,
                                            last_price=float(candle["close"])):
                    status = PAPER_OPEN
                    trade["status"] = PAPER_OPEN
                    trade["entry_price"] = float(trade["entry"])
                    worst = best = 0.0
                    self._event(result, trade, "ENTRY_OPENED", price=float(trade["entry"]),
                                candle_ts=ts, reason="entry level traded")
                    result.opened += 1
                else:
                    # Another runner claimed this same entry.  It will safely
                    # handle the result; avoid duplicate lifecycle decisions.
                    return

            if status != PAPER_OPEN:
                continue
            mae, mfe = self._excursion(trade, candle)
            if mae is not None:
                worst = max(worst or 0.0, mae)
                best = max(best or 0.0, mfe)
            event = exit_event(trade, candle)
            if event is None:
                continue

            # The trade is now filled, so its scan must be EXECUTED before it
            # can transition to CLOSED.  (This is normally already true.)
            if not self._ensure_executed(int(trade["scan_id"]), trade):
                self.db.cancel_paper_trade(int(trade["id"]), reason="scan no longer executable", closed_ts=self.clock_ms())
                self._event(result, trade, "CANCELLED", reason="scan no longer executable")
                result.cancelled += 1
                return
            if self.db.close_paper_trade(
                int(trade["id"]), outcome=event["outcome"], exit_price=event["exit_price"],
                rr_achieved=event["rr_achieved"], close_reason=event["reason"],
                closed_ts=ts, last_candle_ts=ts, last_price=float(candle["close"]),
                mae=worst, mfe=best,
            ):
                note = (f"paper runner {event['reason']} at {event['exit_price']:,.8g}; "
                        f"outcome {event['outcome']} ({event['rr_achieved']:+.2f}R)")
                try:
                    self.db.update_status(int(trade["scan_id"]), "CLOSED", note=note,
                                          reviewer="paper_runner")
                except LifecycleError:
                    # The durable paper result is still correct if a human
                    # closed it simultaneously; do not turn that race into a
                    # false failure.
                    pass
                self._event(result, trade, "CLOSED", candle_ts=ts, **event)
                result.closed += 1
            return

        self.db.touch_paper_trade(int(trade["id"]), last_candle_ts=last_ts,
                                  last_price=last_price, checked_ts=self.clock_ms(),
                                  mae=worst, mfe=best)

    def run_once(self, symbol: Optional[str] = None, enroll: bool = True) -> PaperRun:
        """Enroll approved signals and evaluate every active paper position once."""
        symbol = normalize_symbol(symbol) if symbol else None
        result = PaperRun()
        if enroll:
            self.enroll_approved(symbol=symbol, result=result)
        for trade in self.db.active_paper_trades(symbol=symbol):
            try:
                self._process_trade(trade, result)
            except Exception as exc:  # one bad network symbol must not stop others
                result.errors.append({"trade_id": trade.get("id"), "scan_id": trade.get("scan_id"),
                                      "symbol": trade.get("symbol"),
                                      "error": f"{type(exc).__name__}: {exc}"})
        return result
