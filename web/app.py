"""web/app.py

CryptoBrain — all-in-one dashboard ("watch everything, click to decide").

One page shows:
  • live signal + lifecycle badge + approve/reject/execute/close buttons
  • multi-condition plans with confidence bars
  • market context (funding / OI / L/S)
  • full feature snapshot + score breakdown
  • human approval queue (clickable Approve / Reject)
  • recent signal history (click a row → detail modal, decide from there)
  • learning dashboard (backtest win-rates + calibration profile)
  • coach panel (explain / mentor / personal feedback)
  • LLM narrative + raw JSON

Endpoints
  GET /             dashboard HTML
  GET /api/scan     live brain output (persisted, deduped by signal_id)
  GET /api/pending  signals awaiting human approval
  POST /api/review  approve/reject/execute/close a signal
  GET /api/history  recent scans with lifecycle status
  GET /api/signal   full detail + plans + decision trail for one scan
  GET /api/learning backtest stats + calibration profile + plan distribution
  GET /api/paper    paper-trade state, outcomes, and runner statistics
  POST /api/paper/run  enroll/check approved paper trades once
  GET /api/coach    explain + mentor + personal feedback
  GET /api/health   full system health (data feeds, DB, risk gate, MCP, LLM)
  GET /api/agents   desk morning briefing (every watchlist asset + gate + queue)
  GET /api/mcp      MCP server availability + tools
  POST /api/ask     natural-language question to the desk
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, render_template_string, request

from config import SYMBOL, SYMBOLS, TIMEFRAME, BARS, MIN_CONFIDENCE, DEFAULT_RISK_REWARD, DASHBOARD_HOST, DASHBOARD_PORT, VERSION
from data.symbols import normalize_symbol, resolve_symbol, symbol_choices
from data.sample_client import maybe_client
from data.binance_client import BinanceClient
from engine.signal_engine import analyze_frame
from output.signal_schema import validate_output

_CACHE: dict = {"payload": None, "ts": 0, "ttl": 40}
_LAST_GOOD: dict[tuple[str, str], dict] = {}   # last successful payload by (symbol, timeframe)


def _sym(value: str | None) -> str:
    return normalize_symbol(value or SYMBOL)


def _persist(payload: dict) -> tuple[int, str]:
    """Save the scan to the learning DB (deduped by signal_id) and return
    (scan_id, lifecycle_status)."""
    from data.database import SignalDB
    from engine.lifecycle import reviewable
    sig = payload.get("signal", {})
    decision = payload.get("decision") or {}
    # Desk-first (decision A4): desk-vetoed signals never enter the queue.
    desk_ok = decision.get("action") in ("BUY", "SELL") if decision else True
    status_override = None if desk_ok else "CREATED"
    with SignalDB() as db:
        existing = db.conn.execute(
            "SELECT id, status FROM scans WHERE signal_id=?", (sig.get("signal_id"),)
        ).fetchone()
        if existing:
            scan_id, status = existing["id"], existing["status"]
        else:
            scan_id = db.save_scan(payload, status_override=status_override)
            status = "PENDING_REVIEW" if reviewable(sig) and desk_ok else "CREATED"
    payload["scan_id"] = scan_id
    if status == "CREATED" and decision and decision.get("blocked_by"):
        note = "DESK BLOCKED — " + "; ".join(decision["blocked_by"])
    else:
        note = ("awaiting human approval — click Approve / Reject"
                if status == "PENDING_REVIEW" else
                "monitor-only signal (no action required)" if status == "CREATED" else
                f"current state: {status}")
    payload["lifecycle"] = {"status": status, "note": note}
    return scan_id, status


_SCAN_TIMEOUT = 45  # seconds — hard cap on a full analysis; fall back below


def _basic_scan(symbol: str, tf: str, save: bool) -> dict:
    """Engine-only quick scan (no MTF/context) — the guaranteed fallback so the
    dashboard always shows data even if external sources are unreachable."""
    import threading
    symbol = _sym(symbol)
    result: dict = {}

    def _work():
        try:
            client = maybe_client()
            df = client.klines(symbol, tf, bars=BARS)
            out = analyze_frame(df, symbol=symbol, timeframe=tf,
                                min_confidence=MIN_CONFIDENCE,
                                default_rr=DEFAULT_RISK_REWARD)
            payload = out.as_json()
            payload["market_context"] = client.market_context(symbol)
            payload["market_context"]["fallback_note"] = "futures not fully checked (fallback mode)"
            try:
                from brain.trading_intelligence import build_intelligence
                payload["intelligence"] = build_intelligence(payload, df=df)
            except Exception:
                pass
            payload["validation"] = validate_output(payload)
            payload["degraded"] = True
            payload["degraded_reason"] = "full analysis timed out — showing engine-only signal"
            result["payload"] = payload
        except Exception as exc:  # pragma: no cover
            result["error"] = f"{type(exc).__name__}: {exc}"

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout=_SCAN_TIMEOUT)
    if "payload" in result:
        payload = result["payload"]
        if save:
            try:
                _persist(payload)
            except Exception:
                pass
        _LAST_GOOD[(symbol, tf)] = payload
        return payload
    raise ConnectionError(result.get("error", "scan timed out"))


def compute_payload(symbol: str, tf: str, save: bool = True, use_cache: bool = True) -> dict:
    symbol = _sym(symbol)
    if use_cache and _CACHE["payload"] and _CACHE["payload"].get("signal", {}).get("asset") == symbol \
            and _CACHE["payload"].get("signal", {}).get("timeframe") == tf \
            and time.time() - _CACHE["ts"] < _CACHE["ttl"]:
        return _CACHE["payload"]

    # Run the full analysis (MTF + context + styles + memory) in a watchdog
    # thread: if it exceeds _SCAN_TIMEOUT we fall back to the engine-only scan.
    import threading
    result: dict = {}

    def _work():
        try:
            from brain.full_pipeline import analyze_full
            client = maybe_client()
            payload = analyze_full(symbol, tf, bars=BARS, client=client,
                                   with_context=True, with_memory=True)
            payload["validation"] = validate_output(payload)
            result["payload"] = payload
        except Exception as exc:  # pragma: no cover
            result["error"] = f"{type(exc).__name__}: {exc}"

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout=_SCAN_TIMEOUT)

    if "payload" not in result:
        # Fallback: if we have a last-good payload for THIS asset/timeframe,
        # serve it marked stale so the dashboard never blanks on transient
        # failures. Never show BTC data while the user selected ETH/XAU.
        key = (symbol, tf)
        if key in _LAST_GOOD:
            stale = dict(_LAST_GOOD[key])
            stale["stale"] = True
            stale["stale_error"] = result.get("error", "scan timed out")
            _CACHE.update(payload=stale, ts=time.time())
            return stale
        try:
            payload = _basic_scan(symbol, tf, save=save)
        except Exception as exc:
            spec = resolve_symbol(symbol)
            payload = {
                "signal": {"signal_id": f"{symbol}_{int(time.time()*1000)}",
                           "timestamp": int(time.time()*1000), "asset": symbol,
                           "action": "NO TRADE", "entry": None, "stop_loss": None,
                           "take_profit": None, "risk_reward": 0,
                           "confidence": "LOW", "timeframe": tf,
                           "reason": f"scan failed: {exc}", "signal_type": "ERROR"},
                "plans": [], "snapshot": {"features": {}, "scores": {}},
                "market_context": {"symbol": spec.symbol, "data_symbol": spec.data_symbol,
                                   "market": spec.market, "provider": spec.provider,
                                   "futures": False, "note": spec.note or "not checked"},
                "error": str(exc), "lifecycle": {"status": "ERROR", "note": str(exc)},
            }
        _CACHE.update(payload=payload, ts=time.time())
        return payload

    payload = result["payload"]
    if save:
        try:
            _persist(payload)
        except Exception:
            pass
    _LAST_GOOD[(symbol, tf)] = payload
    _CACHE.update(payload=payload, ts=time.time())
    return payload


def build_payload(symbol: str, tf: str) -> dict:
    return compute_payload(symbol, tf, save=True, use_cache=True)


# Keep this a normal triple-quoted string so the lightweight CI extractor can
# read it directly. Embedded JavaScript deliberately uses `nl` below instead
# of a `\n` string escape, which stays correct without a raw Python literal.
HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CryptoBrain — All-in-One</title>
<link rel="icon" href="data:,">
<style>
  :root{--bg:#0b0f17;--card:#131a26;--line:#223045;--txt:#e6edf7;--mut:#8aa0bd;
        --green:#22c55e;--red:#ef4444;--amber:#f59e0b;--blue:#3b82f6}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--txt);
      font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;padding:22px}
  h1{font-size:19px;margin:0} .sub{color:var(--mut);font-size:13px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(350px,1fr));gap:14px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
  .card h2{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--mut);margin:0 0 10px}
  .pill{padding:3px 10px;border-radius:999px;font-weight:700;font-size:13px}
  .BUY{background:rgba(34,197,94,.15);color:var(--green);border:1px solid var(--green)}
  .SELL{background:rgba(239,68,68,.15);color:var(--red);border:1px solid var(--red)}
  .NOTRADE{background:rgba(139,160,189,.12);color:var(--mut)}
  .badge{font-size:11px;padding:2px 8px;border-radius:6px;background:var(--line);border:1px solid transparent}
  table{width:100%;border-collapse:collapse;font-size:12.5px}
  th,td{text-align:left;padding:5px 7px;border-bottom:1px solid var(--line)}
  th{color:var(--mut);font-weight:500}
  .kv{display:grid;grid-template-columns:auto 1fr;gap:3px 12px;font-size:12.5px}
  .kv b{color:var(--mut);font-weight:500}
  .plan{border:1px solid var(--line);border-radius:10px;padding:9px 11px;margin-bottom:9px}
  .plan .h{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}
  .plan .c{color:var(--mut);margin-top:5px;font-size:12px}
  .bar{height:6px;border-radius:6px;background:#1b2740;margin-top:7px;overflow:hidden}
  .bar i{display:block;height:100%}
  .rowbtn{background:var(--blue);border:none;color:#fff;padding:5px 10px;border-radius:8px;cursor:pointer;font:inherit;font-size:12px}
  .rowbtn:hover{opacity:.9}
  .ok{background:rgba(34,197,94,.9);color:#04120a} .no{background:rgba(239,68,68,.9);color:#fff}
  input,select,textarea{background:#0e1524;border:1px solid var(--line);color:var(--txt);padding:6px 8px;border-radius:8px;font:inherit}
  .flex{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  .muted{color:var(--mut)} .mono{font-variant-numeric:tabular-nums}
  .row{display:flex;justify-content:space-between;gap:10px;align-items:center;padding:6px 4px;border-bottom:1px solid var(--line);cursor:pointer;flex-wrap:wrap}
  .row:hover{background:#0e1524}
  .row .btns{display:flex;gap:6px}
  #modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:50;padding:30px;overflow:auto}
  #modal .box{background:var(--card);border:1px solid var(--line);border-radius:14px;max-width:760px;margin:auto;padding:20px}
  .err{color:var(--red)} .okc{color:var(--green)}
  .note{font-size:11px;color:var(--mut)}
</style></head><body>
<div class="flex" style="justify-content:space-between;margin-bottom:16px">
  <div><h1>🧠 CryptoBrain — All-in-One <span class="badge" style="background:var(--line)">v{{version}}</span></h1>
  <div class="sub">watch everything · click to approve · the engine learns from you</div></div>
  <div class="flex">
    <input id="sym" list="symbols" value="{{symbol}}" size="10" title="BTCUSDT, ETHUSDT, XAUUSD/GOLD, or any Binance USDT pair">
    <datalist id="symbols">
      {% for s in symbols %}<option value="{{s.symbol}}">{{s.label}}{% if s.data_symbol != s.symbol %} · {{s.data_symbol}}{% endif %}</option>{% endfor %}
    </datalist>
    {% for s in symbols %}<button class="rowbtn" onclick="setSymbol('{{s.symbol}}')">{{s.symbol}}</button>{% endfor %}
    <select id="tf">
      {% for t in ['1m','5m','15m','30m','1h','4h','1d','1w','1M'] %}<option value="{{t}}" {{'selected' if t==tf}}>{{t}}</option>{% endfor %}
    </select>
    <button class="rowbtn" onclick="load(true)">Refresh</button>
    <label class="note"><input type="checkbox" id="auto" checked> auto</label>
    <span id="updated" class="note"></span>
  </div>
</div>
<div id="app" class="grid">Loading…</div>
<noscript><div class="card" style="grid-column:1/-1"><div class="err">JavaScript is disabled — the dashboard needs JS. Enable it and reload.</div></div></noscript>
<div id="modal"><div class="box" id="mbody"></div></div>

<script>
window.onerror = function(msg, src, line){
  try{
    var box=document.createElement('div');
    box.style.cssText='position:fixed;top:0;left:0;right:0;background:#ef4444;color:#fff;z-index:999;padding:10px;font:12px monospace;white-space:pre-wrap';
    box.textContent='⚠️ Dashboard script error: '+msg+' (line '+line+'). Press Ctrl+F5 to hard-refresh. If it persists, run `python main.py scan` in the terminal and share the output.';
    document.body.appendChild(box);
  }catch(e){}
};
window.addEventListener('unhandledrejection', function(ev){
  try{
    var box=document.createElement('div');
    box.style.cssText='position:fixed;bottom:0;left:0;right:0;background:#b45309;color:#fff;z-index:999;padding:8px;font:12px monospace;white-space:pre-wrap';
    box.textContent='⚠️ Background update hiccup: '+(ev.reason&&ev.reason.message||ev.reason||'unknown')+' — the page keeps working.';
    document.body.appendChild(box);
  }catch(e){}
});
const fmt=(v,n=2)=> v==null?'—':Number(v).toLocaleString(undefined,{minimumFractionDigits:n,maximumFractionDigits:n});
const cls=a=> a==='BUY'?'BUY':a==='SELL'?'SELL':'NOTRADE';
const stCls=s=> s==='APPROVED'?'var(--green)':s==='REJECTED'?'var(--red)':s==='EXECUTED'?'var(--blue)':s==='CLOSED'?'var(--amber)':'var(--amber)';
const esc=x=> String(x??'').replace(/&/g,'&amp;').replace(/</g,'&lt;');
function setSymbol(sym){ const el=document.getElementById('sym'); if(el) el.value=sym; load(true); }

function card(title, inner, span){ return `<div class="card" ${span?'style="grid-column:1/-1"':''}><h2>${title}</h2>${inner}</div>`; }

function render(d){
  const s=d.signal||{}, plans=d.plans||[], snap=d.snapshot||{}, f=snap.features||{}, sc=snap.scores||{}, ctx=d.market_context||{}, lc=d.lifecycle||{};
  const cf=sc.bull?.score??0, cs=sc.bear?.score??0;
  const sid=d.scan_id, st=lc.status||'';
  const decideBtns = st==='PENDING_REVIEW'
    ? `<div class="flex" style="margin-top:8px">
         <input id="note-${sid}" placeholder="note (optional)" style="flex:1">
         <button class="rowbtn ok" onclick="decide(${sid},'APPROVED')">✓ Approve</button>
         <button class="rowbtn no" onclick="decide(${sid},'REJECTED')">✗ Reject</button></div>`
    : (st==='APPROVED' ? `<div class="flex" style="margin-top:8px">
         <button class="rowbtn" onclick="decide(${sid},'EXECUTED')">▶ Mark executed</button>
         <button class="rowbtn no" onclick="decide(${sid},'SKIPPED')">Skip</button></div>`
       : (st==='EXECUTED' ? `<button class="rowbtn" style="background:var(--amber)" onclick="decide(${sid},'CLOSED')">✔ Close (record outcome)</button>` : ''));

  let html = card('LIVE SIGNAL', `
    <div class="flex" style="margin-bottom:6px">
      <span class="pill ${cls(s.action)}">${s.action}</span>
      <b>${s.asset} · ${s.timeframe}</b>
      <span class="badge">${s.signal_id||''}</span>
      <span class="badge">${s.confidence}</span>
      ${s.risk_reward?`<span class="badge">RR ${s.risk_reward}</span>`:''}
      <span class="badge" style="background:${stCls(st)}22;border-color:${stCls(st)}">${st||'—'}</span>
    </div>
    <div class="kv">
      <b>Entry</b><span class="mono">${fmt(s.entry)}</span>
      <b>Stop loss</b><span class="mono">${fmt(s.stop_loss)}</span>
      <b>Take profit</b><span class="mono">${fmt(s.take_profit)}</span>
      ${ctx.data_symbol&&ctx.data_symbol!==s.asset?`<b>Data source</b><span>${esc(ctx.data_symbol)} (${esc(ctx.provider||'provider')})</span>`:''}
      <b>Reason</b><span>${esc(s.reason)}</span>
      <b>Note</b><span>${esc(lc.note||'')}</span>
    </div>${decideBtns}`, true);

  const dec=d.decision||{};
  if(dec.action){
    const dc=dec.gates||{}, pb=dc.playbook||{}, rg=dc.risk||{}, pv=dc.portfolio||{};
    const decOk = dec.action==='BUY'||dec.action==='SELL';
    html += card('PROFESSIONAL DESK DECISION', `
      <div class="flex" style="margin-bottom:8px">
        <span class="pill ${decOk?'': 'NOTRADE'}">${decOk?('TRADE '+dec.action):'WAIT — NO TRADE'}</span>
        <span class="badge">${esc(dec.decision_text||'')}</span>
        <span class="badge">${esc(dec.regime_label||'')}</span>
        <span class="badge">setup: ${esc(dec.plan_type||'—')}</span>
      </div>
      ${pb.name?`<div class="kv"><b>Playbook</b><span>${esc(pb.name)} — ${esc(pb.note||'')}</span></div>`:''}
      ${(pb.checks||[]).map(c=>`<div class="note" style="margin-top:2px">${c.ok?'✓':'✗'} ${esc(c.detail)}</div>`).join('')}
      ${(rg.blocked_by||[]).map(b=>`<div class="err">✗ risk: ${esc(b)}</div>`).join('')}
      ${(pv.reasons||[]).map(b=>`<div class="err">✗ portfolio: ${esc(b)}</div>`).join('')}
      ${dec.blocked_by&&dec.blocked_by.length?`<div class="err" style="margin-top:6px">BLOCKED — ${dec.blocked_by.map(esc).join('; ')}</div>`:''}
      <div class="note" style="margin-top:6px">The desk decision is the only output you act on. The engine signal above is research.</div>`, true);
  }

  const intel=d.intelligence||{};
  const scard=intel.signal_card||{};
  if(intel.asset){
    const fchecks=intel.trade_filter||{};
    const tpl=scard.tp_ladder||[];
    const tplHtml=tpl.length ? tpl.map(t=>`<tr><td><b>${esc(t.target)}</b></td><td class="mono">$${fmt(t.price)}</td><td style="color:var(--green)">+${fmt(t.gain_pct)}%</td><td>${t.allocation_pct}% size</td><td class="note">${esc(t.management||'')}</td></tr>`).join('') : '';
    const kelly=intel.kelly_criterion||{};

    html += card('INSTITUTIONAL AI SIGNAL CARD v2.0 — Enterprise Alpha Intelligence', `
      <div class="flex" style="margin-bottom:10px;justify-content:space-between;border-bottom:1px solid var(--line);padding-bottom:8px">
        <div class="flex">
          <span class="pill ${cls(intel.signal)}">${esc(intel.signal)}</span>
          <b>${esc(intel.asset)} · ${esc(intel.timeframe)}</b>
          <span class="badge" style="background:var(--blue);color:#fff;font-weight:700">Grade ${esc(intel.trade_quality_grade||'N/A')}</span>
        </div>
        <div class="flex">
          <span class="badge" style="background:rgba(59,130,246,.15);color:var(--blue)">IPS ${intel.institutional_probability_score??intel.confidence??0}/100</span>
          <span class="badge" style="background:rgba(34,197,94,.15);color:var(--green)">AI Confidence ${scard.ai_confidence_index||(intel.confidence+'%')} ${scard.confidence_delta||''}</span>
        </div>
      </div>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-bottom:10px">
        <div class="kv">
          <b>Entry Zone</b><span class="mono" style="color:var(--txt);font-weight:700">${esc(intel.entry_zone||'N/A')}</span>
          <b>Stop Loss</b><span class="mono" style="color:var(--red)">${esc(scard.stop_loss_display||fmt(intel.stop_loss))}</span>
          <b>Risk:Reward</b><span><b>${esc(intel.risk_reward||'1:2.0')}</b></span>
          <b>Hold Time</b><span>${esc(intel.expected_hold_time||'4–8 Hours')}</span>
          <b>Active Until</b><span class="note">${esc(intel.active_until||'N/A')}</span>
        </div>
        <div class="kv">
          <b>Market Regime</b><span>${esc(intel.regime?.label||intel.trend)}</span>
          <b>Liquidity Trap</b><span>${intel.regime?.trap_detected?'<span class="err">⚠️ Trap / Fakeout Risk</span>':'<span class="okc">✓ Clean / No Trap</span>'}</span>
          <b>Order Flow SMC</b><span>OB: ${esc(intel.order_block)} · FVG: ${esc(intel.fair_value_gap)}</span>
          <b>Kelly Sizing</b><span>${kelly.recommended_risk_pct?kelly.recommended_risk_pct+'% risk ($'+fmt(kelly.recommended_risk_amt)+')':'Fixed 1.0%'} · ${esc(kelly.recommended_leverage||'1x-3x')}</span>
          <b>Capital Preserv.</b><span>${esc(intel.self_review?.capital_preservation_decision||'Capital protected')}</span>
        </div>
      </div>
      ${tplHtml ? `<div style="margin:10px 0"><b>Smart Take-Profit Ladder</b><table style="margin-top:4px"><tr><th>Target</th><th>Price</th><th>Gain</th><th>Allocation</th><th>Action / Management</th></tr>${tplHtml}</table></div>` : ''}
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-top:10px">
        <div style="background:#0e1524;padding:10px;border-radius:8px;border:1px solid var(--line)">
          <b style="color:var(--green)">Why AI Took This Trade:</b>
          <div class="note" style="margin-top:6px">${(scard.why_ai_took_trade||intel.reason||[]).slice(0,4).map(r=>'• '+esc(r)).join('<br>')}</div>
        </div>
        <div style="background:#0e1524;padding:10px;border-radius:8px;border:1px solid var(--line)">
          <b style="color:var(--red)">Invalidation Conditions:</b>
          <div class="note" style="margin-top:6px">${(scard.invalidation_conditions||[]).slice(0,3).map(r=>'• '+esc(r)).join('<br>')}</div>
        </div>
      </div>
      <div style="background:#0e1524;padding:10px;border-radius:8px;border:1px solid var(--line);margin-top:10px">
        <b>Alternative Scenario:</b>
        <div class="note" style="margin-top:4px">${esc(scard.alternative_scenario||intel.scenario_B||'')}</div>
      </div>
      <details style="margin-top:8px"><summary>Desk Filter Checks & Full Scenarios</summary>
        <div class="note" style="margin-top:6px">Scenario A: ${esc(intel.scenario_A)}<br>Scenario B: ${esc(intel.scenario_B)}<br>Scenario C: ${esc(intel.scenario_C)}</div>
        <table style="margin-top:6px"><tr><th>Filter Check</th><th>Status</th><th>Value</th></tr>${Object.entries(fchecks).map(([k,v])=>`<tr><td>${esc(k)}</td><td style="color:${v.ok?'var(--green)':'var(--red)'}">${v.ok?'✓ PASS':'✗ FAIL'}</td><td>${esc(v.value??'')}</td></tr>`).join('')}</table>
      </details>`, true);
  }

  html += card(`CANDLESTICK — ${s.asset} ${s.timeframe}`,
    `<div id="chart" style="width:100%;height:260px"></div>
     <div class="note">last 90 candles · EMA 20/50 overlay · volume bars · HTF levels dotted</div>`, true);

  const mtf=d.mtf||{}, views=mtf.views||{}, al=mtf.alignment||{};
  html += card('MULTI-TIMEFRAME (HTF → LTF)', `
    <table><tr><th>TF</th><th>Trend</th><th>RSI</th><th>ADX</th><th>Event</th><th>Zone</th></tr>
    ${['1M','1w','1d','4h','1h','30m','15m','5m','1m'].map(tf=>{
      const v=views[tf]||{};
      if(!v.available) return `<tr><td>${tf}</td><td class="muted">—</td><td/><td/><td/><td/></tr>`;
      return `<tr><td><b>${tf}</b></td>
        <td style="color:${v.trend==='bull'?'var(--green)':v.trend==='bear'?'var(--red)':'var(--amber)'}">${v.trend}</td>
        <td>${fmt(v.rsi,0)}</td><td>${fmt(v.adx,0)}</td>
        <td>${v.event_kind||'—'}</td><td>${v.premium_discount||'—'}</td></tr>`;
    }).join('')}
    </table>
    <div class="flex" style="margin-top:8px">
      <span class="badge">HTF ${mtf.htf_bias}</span>
      <span class="badge">LTF ${mtf.ltf_bias}</span>
      <span class="badge" style="color:${al.score>=30?'var(--green)':al.score<=-30?'var(--red)':'var(--amber)'}">alignment ${al.score} (${al.label})</span>
    </div>
    <div class="note" style="margin-top:6px">support ${(mtf.key_levels?.support||[]).map(fmt).join(', ')} · resistance ${(mtf.key_levels?.resistance||[]).map(fmt).join(', ')}</div>`, true);

  html += card(`CONDITIONAL PLANS (${plans.length})`,
    plans.length ? plans.map(p=>`<div class="plan"><div class="h">
        <b class="${cls(p.action)}">${p.action} · ${p.type}</b>
        <span class="badge">${p.confidence}% ${p.confidence_label}</span></div>
        <div class="bar"><i style="width:${p.confidence}%;background:${p.confidence>=80?'var(--green)':p.confidence>=60?'var(--amber)':'var(--red)'}"></i></div>
        <div class="c">${esc(p.condition)}</div>
        <div class="c mono">entry ${fmt(p.entry)} · sl ${fmt(p.stop_loss)} · tp ${p.take_profits.map(fmt).join(', ')} · RR ${p.risk_reward}</div></div>`).join('')
      : '<div class="muted">No plans above threshold — best trade is no trade.</div>');

  const styles=d.styles||{}, sall=styles.styles||{};
  html += card('WHAT THE MARKET OFFERS (by trading style)', `
    <table><tr><th>Style</th><th>Setup</th><th>Conf</th><th>Horizon</th></tr>
    ${['Scalp','Day','Swing','Momentum','Position'].map(s=>{
      const v=sall[s]||{};
      if(!v.available) return `<tr><td>${s}</td><td class="muted">—</td><td/><td/></tr>`;
      return `<tr><td><b>${s}</b></td>
        <td><span class="${cls(v.direction)}">${v.direction}</span> <span class="note">${esc((v.reason||'').slice(0,55))}</span></td>
        <td>${v.confidence}%</td><td>${v.horizon}</td></tr>`;
    }).join('')}
    </table>
    ${styles.market_offering && styles.market_offering.length
      ? `<div class="note" style="margin-top:6px">offering: ${styles.market_offering.join(', ')}</div>`
      : `<div class="muted" style="margin-top:6px">stand aside — ${esc((styles.stand_aside||[]).join('; '))}</div>`}`, true);

  const kvs=[
    ['Trend', f.trend, null], ['EMA stack', f.ema_alignment_bull?'Bull aligned':f.ema_alignment_bear?'Bear aligned':'mixed'],
    ['Supertrend', f.supertrend_bull?'Bull':'Bear'], ['ADX', fmt(f.adx,1)],
    ['RSI', fmt(f.rsi,1)], ['RSI div bull/bear', `${f.rsi_divergence?.bull??0}/${f.rsi_divergence?.bear??0}`],
    ['MACD hist', fmt(f.macd_hist,4)], ['WaveTrend', `${fmt(f.wt1,2)}/${fmt(f.wt2,2)}`],
    ['Volume ratio', fmt(f.volume_ratio,2)], ['vs VWAP', f.above_vwap?'above':'below'],
    ['Structure event', f.event_kind||'—'], ['Bias', f.trend_bias],
    ['Bull OB near', fmt(f.nearest_bull_ob)], ['Bear OB near', fmt(f.nearest_bear_ob)],
    ['FVG bull/bear', `${f.fvg_bull_count}/${f.fvg_bear_count}`],
    ['Premium/Discount', f.premium_discount], ['ATR %', fmt(f.atr_pct,3)],
    ['Sweep', f.sweep?`${f.sweep.side} @ ${fmt(f.sweep.level)}`:'none'],
  ];
  html += card('FEATURE SNAPSHOT', `<div class="kv">${kvs.map(([k,v])=>`<b>${k}</b><span>${v}</span>`).join('')}</div>`);

  html += card('SCORE BREAKDOWN', `<table><tr><th>Condition</th><th>Bull</th><th>Bear</th></tr>${
    [...new Set([...Object.keys(sc.bull?.conditions||{}),...Object.keys(sc.bear?.conditions||{})])]
      .map(k=>`<tr><td>${k}</td><td>${sc.bull?.conditions?.[k]??0}</td><td>${sc.bear?.conditions?.[k]??0}</td></tr>`).join('')
    }<tr><td><b>Total</b></td><td><b>${cf}</b></td><td><b>${cs}</b></td></tr></table>
    <div class="muted" style="margin-top:6px">${(sc.bull?.reasons||[]).concat(sc.bear?.reasons||[]).slice(0,6).map(r=>'• '+esc(r)).join('<br>')}</div>`);

  html += card('MARKET CONTEXT', `<div class="kv">
    <b>Provider symbol</b><span>${ctx.data_symbol?esc(ctx.data_symbol)+' · '+esc(ctx.provider||'provider'):'n/a'}</span>
    <b>Market</b><span>${esc(ctx.market||'n/a')}</span>
    <b>Funding</b><span>${ctx.funding_rate_pct!=null?ctx.funding_rate_pct+'%':'n/a'}</span>
    <b>Open interest</b><span>${ctx.open_interest!=null?fmt(ctx.open_interest,0):'n/a'}</span>
    <b>L/S ratio</b><span>${ctx.long_short_ratio??'n/a'}</span>
    <b>24h change</b><span>${ctx.liq_24h_change_pct!=null?ctx.liq_24h_change_pct+'%':'n/a'}</span>
    <b>Futures</b><span>${ctx.futures?'available':esc(ctx.note||'not available')}</span>
  </div>`);

  const mctx=d.context||{};
  const fng=mctx.fear_greed||{}, dom=mctx.dominance||{}, eq=mctx.equities||{},
        macro=mctx.macro||{}, cyc=mctx.cycle||{}, geo=mctx.geopolitics||{},
        soc=mctx.social||{}, reg=mctx.risk_regime||{};
  const cp=eq.change_pct||{};
  html += card('CONTEXT — WHAT AFFECTS PRICE', `
    <div class="kv">
      <b>Risk regime</b><span>${reg.regime||'n/a'} (${reg.score??''})</span>
      <b>Fear & Greed</b><span>${fng.available?fng.value+' ('+fng.label+')':'n/a'}</span>
      <b>BTC dominance</b><span>${dom.available?dom.btc_dominance+'% (ETH '+dom.eth_dominance+'%)':'n/a'}</span>
      <b>Market cap</b><span>${dom.available?'$'+(dom.total_market_cap_usd/1e12).toFixed(2)+'T ('+dom.market_cap_change_24h_pct+'%)':'n/a'}</span>
      <b>S&P500 / Nasdaq</b><span>${eq.available?cp['^spx']+'% / '+cp['^ndq']+'%':'n/a'}</span>
      <b>Dollar (DXY)</b><span>${eq.available?cp['dx.f']+'%':'n/a'}</span>
      <b>Cycle phase</b><span>${cyc.available?cyc.phase+' · '+cyc.days_since_halving+'d since halving':'n/a'}</span>
      <b>Macro events</b><span>${macro.available?(macro.events||[]).slice(0,2).map(e=>e.name+' '+e.date+' ('+e.days_until+'d)'+(e.days_until<=2?' ⚠️':'')).join('<br>')||'none soon':'n/a'}</span>
      <b>Geopolitics</b><span>${geo.available&&geo.count&&geo.hits&&geo.hits[0]?geo.count+' headline hit(s) ⚠️ — '+esc(geo.hits[0].keyword):'calm'}</span>
      <b>Social/Influencer</b><span>${soc.available&&soc.count&&soc.influencer_mentions&&soc.influencer_mentions[0]?soc.count+' mention(s) — '+esc(soc.influencer_mentions[0].keyword):(soc.available&&soc.count?soc.count+' mention(s)':'quiet')}</span>
    </div>`);

  const mem=d.memory||{};
  html += card('STATE MEMORY — SIGNAL STABILITY', `
    <div class="kv">
      <b>Status</b><span>${mem.status||'—'}</span>
      ${mem.stable_since?`<b>Stable since</b><span>${new Date(mem.stable_since).toLocaleTimeString()}</span>`:''}
      ${mem.reaffirms?`<b>Reaffirmed</b><span>${mem.reaffirms}× (same state — no new signal)</span>`:''}
      ${mem.flips_1h?`<b>HTF flips (1h)</b><span>${mem.flips_1h}</span>`:''}
    </div>
    ${(mem.changes||[]).length?`<div class="note" style="margin-top:6px">${mem.changes.map(c=>'• '+esc(c)).join('<br>')}</div>`:''}
    ${mem.whipsaw?`<div class="err" style="margin-top:6px">⚠️ Whipsaw guard active — signals suppressed until market settles.</div>`:''}
    <div class="note" style="margin-top:6px">Signals change only when the market STATE changes — not every 30s refresh.</div>`);

  html += card('CONNECTIONS', `<div id="conn">loading…</div>
    <div class="flex" style="margin-top:8px">
      <button class="rowbtn" onclick="systemCheck()">🔍 System check</button>
      <span id="diagmsg" class="note"></span>
    </div>
    <div id="diag" class="note" style="margin-top:6px"></div>`);
  html += card('HUMAN APPROVAL QUEUE', '<div id="queue">loading…</div>');
  html += card('RISK & DISCIPLINE GATE', '<div id="riskgate">loading…</div>', true);
  html += card('SYSTEM HEALTH', '<div id="health">loading…</div>');
  html += card('AGENTS — MORNING BRIEFING', '<div id="agents">loading…</div>', true);
  html += card('MCP SERVER', '<div id="mcp">loading…</div>');
  html += card('ASK THE DESK', `<div class="flex"><input id="askq" placeholder="ask the desk… e.g. is the risk gate open? / scan BTC / what's pending?" style="flex:1" onkeydown="if(event.key==='Enter')askDesk()">
    <button class="rowbtn" onclick="askDesk()">Ask</button></div>
    <div id="askout" class="note" style="white-space:pre-wrap;margin-top:8px"></div>`, true);
  html += card('PAPER TRADING — LIVE OUTCOME RUNNER', '<div id="paper">loading…</div>', true);
  html += card('RECENT SIGNALS', '<div id="hist">loading…</div>');
  html += card('LEARNING — backtest & calibration', '<div id="learn">loading…</div>', true);
  html += card('🧑‍🏫 COACH', `<button class="rowbtn" onclick="coach()">Explain & mentor me</button>
    <div id="coach" class="muted" style="white-space:pre-wrap;margin-top:10px"></div>`, true);
  html += card('LLM NARRATIVE', `<div class="muted" style="white-space:pre-wrap">${esc(d.llm?.narrative)||'Enable LLM_PROVIDER in .env, or use rule-based output.'}</div>`);
  html += card('RAW JSON', `<details><summary>show</summary><pre style="max-height:380px;overflow:auto;font-size:11px">${esc(JSON.stringify(d,null,2))}</pre></details>`);

  document.getElementById('app').innerHTML = html;
  loadPending(); loadPaper(); loadHistory(); loadLearning(); loadSources();
  loadRiskGate(); loadHealth(); loadAgents(); loadMcp();
  const symE=document.getElementById('sym'); const tfE=document.getElementById('tf');
  loadChart((symE?symE.value:'BTCUSDT').toUpperCase(), tfE?tfE.value:'15m');
}

async function loadChart(symbol, tf){
  const host=document.getElementById('chart'); if(!host) return;
  try{
    const d=await (await fetch(`/api/candles?symbol=${symbol}&tf=${tf}&limit=90`)).json();
    if(d.error){ host.innerHTML='<span class="err">'+esc(d.error)+'</span>'; return; }
    host.innerHTML=chartSVG(d.candles||[]);
  }catch(e){ host.innerHTML='<span class="err">chart error</span>'; }
}

function chartSVG(cs){
  if(!cs || !cs.length) return '<span class="muted">no candles</span>';
  const W=820, H=230, padR=14, padT=10, volH=44;
  const prices=cs.flatMap(c=>[+c.high,+c.low]);
  const lo=Math.min(...prices), hi=Math.max(...prices);
  const volMax=Math.max(...cs.map(c=>+c.volume||0));
  const n=cs.length, cw=Math.floor((W-padR)/n);
  const y=p=> padT+(H-volH-padT)*(1-(p-lo)/(hi-lo||1));
  const bodyTop=p=> padT+(H-volH-padT)*(1-(p-lo)/(hi-lo||1));
  // EMA20/50 over closes
  const ema=(span)=>{ const out=[]; let k=2/(span+1), prev=null;
    for(const c of cs){ const v=+c.close; prev=prev==null?v:v+k*(v-prev); out.push(prev); } return out; };
  const e20=ema(20), e50=ema(50);
  let s=`<svg width="100%" viewBox="0 0 ${W} ${H}" style="background:#0e1524;border-radius:8px">`;
  // gridlines
  for(let i=0;i<5;i++){ const p=lo+(hi-lo)*i/4; const yy=y(p);
    s+=`<line x1="0" y1="${yy}" x2="${W}" y2="${yy}" stroke="#1b2740" stroke-width="1"/>
        <text x="4" y="${yy-3}" fill="#5b7290" font-size="9">${fmt(p,0)}</text>`; }
  cs.forEach((c,i)=>{
    const x=i*cw, up=+c.close>=+c.open;
    const col=up?'#22c55e':'#ef4444';
    const oy=bodyTop(+c.open), cy=bodyTop(+c.close);
    const wy=bodyTop(+c.high), ly=bodyTop(+c.low);
    const hb=Math.max(1, Math.abs(cy-oy));
    s+=`<line x1="${x+cw/2}" y1="${wy}" x2="${x+cw/2}" y2="${ly}" stroke="${col}" stroke-width="1"/>
        <rect x="${x+1}" y="${Math.min(oy,cy)}" width="${Math.max(1,cw-3)}" height="${hb}" fill="${col}" opacity="0.9"/>`;
    // volume bar
    const vh=Math.max(1,(+c.volume/volMax)*volH);
    s+=`<rect x="${x+2}" y="${H-vh}" width="${Math.max(1,cw-5)}" height="${vh}" fill="${col}" opacity="0.25"/>`;
  });
  // EMA overlays
  const px=i=>i*cw+cw/2;
  e20.forEach((v,i)=>{ if(i>0) s+=`<line x1="${px(i-1)}" y1="${y(e20[i-1])}" x2="${px(i)}" y2="${y(v)}" stroke="#3b82f6" stroke-width="1.4" opacity="0.85"/>`; });
  e50.forEach((v,i)=>{ if(i>0) s+=`<line x1="${px(i-1)}" y1="${y(e50[i-1])}" x2="${px(i)}" y2="${y(v)}" stroke="#f59e0b" stroke-width="1.2" opacity="0.8"/>`; });
  s+=`<text x="${W-70}" y="${H-volH-6}" fill="#3b82f6" font-size="9">EMA20</text>
      <text x="${W-30}" y="${H-volH-6}" fill="#f59e0b" font-size="9">EMA50</text>`;
  s+='</svg>';
  return s;
}

let __lastGood = '';   // last successfully-rendered dashboard HTML
let __lastGoodAt = null;
async function load(force, quiet){
  const sym=document.getElementById('sym').value.trim().toUpperCase()||'BTCUSDT';
  const tf=document.getElementById('tf').value;
  let t0=Date.now();
  const app=document.getElementById('app');
  if(!quiet && !__lastGood){
    app.innerHTML = `<div class="card" style="grid-column:1/-1"><h2>Scanning ${sym} ${tf}</h2>
      <div class="muted" id="scanstatus">fetching timeframes + news + macro + context…</div></div>`;
  }
  const tick=setInterval(()=>{
    const el=document.getElementById('scanstatus'); if(!el) return;
    const s=((Date.now()-t0)/1000).toFixed(0);
    el.textContent = s<10 ? `fetching timeframes + news + macro + context… (${s}s)` :
                     s<30 ? `still working (${s}s) — your network may be slow, hang on…` :
                            `this is taking long (${s}s) — will show engine-only signal shortly…`;
  }, 1000);
  const ctrl=new AbortController(); const to=setTimeout(()=>ctrl.abort(), 90000);
  try{
    const r=await fetch(`/api/scan?symbol=${sym}&tf=${tf}`+(force?'&force=1':''), {signal:ctrl.signal});
    const d=await r.json(); clearTimeout(to); clearInterval(tick);
    if(d.error || d.stale_error){
      if(quiet && __lastGood){
        // Background refresh failed — KEEP the last good dashboard, just note it.
        const up=document.getElementById('updated');
        if(up) up.textContent='⚠ refresh failed ('+new Date().toLocaleTimeString()+') — showing last data';
        return;
      }
      app.innerHTML=`<div class="card" style="grid-column:1/-1"><div class="err">${esc(d.error||d.stale_error)}</div><button class="rowbtn" onclick="load(true)" style="margin-top:8px">Retry</button></div>`;
      return;
    }
    render(d);
    __lastGood = app.innerHTML;
    __lastGoodAt = Date.now();
    const up=document.getElementById('updated');
    if(up) up.textContent='updated '+new Date().toLocaleTimeString();
  }catch(e){
    clearTimeout(to); clearInterval(tick);
    if(quiet && __lastGood){
      // never wipe a working dashboard on a transient background failure
      const up=document.getElementById('updated');
      if(up) up.textContent='⚠ refresh failed ('+new Date().toLocaleTimeString()+') — showing last data';
      return;
    }
    app.innerHTML=`<div class="card" style="grid-column:1/-1"><div class="err">Load failed: ${esc(e.name==='AbortError'?'timed out (90s)':e)}</div>
      <div class="note" style="margin-top:4px">Click <b>🔍 System check</b> (Connections card) to see which source is blocked, or retry.</div>
      <button class="rowbtn" onclick="load(true)" style="margin-top:8px">Retry</button></div>`;
  }
}

async function loadPending(){
  try{
    const d=await (await fetch('/api/pending')).json();
    const el=document.getElementById('queue'); if(!el) return;
    const q=d.pending||[];
    if(!q.length){ el.innerHTML='<span class="muted">All caught up — no signals waiting 🎉</span>'; return; }
    el.innerHTML=q.map(x=>`<div class="row">
      <span onclick="openModal(${x.id})">#${x.id} <b>${x.symbol}</b> ${x.timeframe} <span class="${cls(x.action)}">${x.action}</span> ${fmt(x.entry)} <span class="note">${esc((x.reason||'').slice(0,50))}</span></span>
      <span class="btns">
        <button class="rowbtn ok" onclick="decide(${x.id},'APPROVED')">✓</button>
        <button class="rowbtn no" onclick="decide(${x.id},'REJECTED')">✗</button>
        <button class="rowbtn" onclick="openModal(${x.id})">🔍</button>
      </span></div>`).join('');
  }catch(e){ const el=document.getElementById('queue'); if(el) el.innerHTML='<span class="err">'+esc(e)+'</span>'; }
}

async function loadPaper(){
  try{
    const d=await (await fetch('/api/paper')).json();
    const el=document.getElementById('paper'); if(!el) return;
    const o=(d.stats||{}).overall||{}, recent=(d.stats||{}).recent||[];
    const wr=o.win_rate!=null?(o.win_rate*100).toFixed(1)+'%':'n/a';
    const rows=recent.slice(0,6).map(t=>`<tr>
      <td>#${t.id} <b>${esc(t.symbol||'')}</b> ${esc(t.action||'')}</td>
      <td>${esc(t.plan_type||'Signal')}</td><td>${esc(t.status||'')}</td>
      <td>${esc(t.outcome||'—')}</td><td>${t.rr_achieved!=null?(+t.rr_achieved).toFixed(2)+'R':'—'}</td>
      <td class="note">${esc(t.close_reason||'')}</td></tr>`).join('');
    el.innerHTML=`<div class="flex" style="margin-bottom:8px">
      <button class="rowbtn" onclick="runPaper()">▶ Check approved paper trades now</button>
      <span id="papermsg" class="note"></span></div>
      <div class="kv"><b>Tracked</b><span>${o.n||0}</span><b>Waiting entry</b><span>${o.waiting||0}</span>
      <b>Open</b><span>${o.open||0}</span><b>Closed</b><span>${o.closed||0}</span>
      <b>Win-rate</b><span>${wr}</span><b>Avg R</b><span>${o.avg_rr??0}</span></div>
      <div class="note" style="margin-top:8px">Paper only: public Binance candles, no API key and no real exchange order. Start <code>python main.py paper --watch</code> for unattended monitoring.</div>
      ${rows?`<table style="margin-top:8px"><tr><th>Trade</th><th>Setup</th><th>Status</th><th>Outcome</th><th>R</th><th>Note</th></tr>${rows}</table>`:'<div class="muted" style="margin-top:8px">Approve a signal, then check it here or start the paper runner.</div>'}`;
  }catch(e){ const el=document.getElementById('paper'); if(el) el.innerHTML='<span class="err">'+esc(e)+'</span>'; }
}

async function loadRiskGate(){
  const el=document.getElementById('riskgate'); if(!el) return;
  try{
    const d=await (await fetch('/api/risk')).json();
    if(d.error){ el.innerHTML='<span class="err">'+esc(d.error)+'</span>'; return; }
    const g=d.gate||{}, det=(g.details||{}), dw=det.drawdown||{}, dws=det.daily_weekly||{},
          eff=det.effective_risk||{}, st=d.trader_state||{}, m=d.metrics||{}, o=(m.overall||{}),
          j=d.journal||{};
    const closed=g.allowed===false;
    const flagBtn=(k,l)=>`<button class="rowbtn ${st[k]?'no':''}" onclick="setTraderState('${k}',${st[k]?0:1})">${l} ${st[k]?'ON':'off'}</button>`;
    let html=`<div class="flex" style="margin-bottom:8px">
        <span class="pill ${closed?'NOTRADE':'BUY'}">${closed?'GATE CLOSED':'GATE OPEN'}</span>
        <span class="badge">progression: ${esc(det.progression?.level||'student')}</span>
        <span class="badge">risk ${eff.risk_pct}% / day ${eff.daily}% / week ${eff.weekly}%</span></div>`;
    html+=`<div class="kv">
        <b>Today</b><span class="mono">${dws.today?.pct!=null?dws.today.pct.toFixed(2)+'% ('+dws.today.n+' trades)':'—'}</span>
        <b>This week</b><span class="mono">${dws.week?.pct!=null?dws.week.pct.toFixed(2)+'%':'—'}</span>
        <b>Drawdown</b><span class="mono">${dw.max_drawdown_pct!=null?dw.max_drawdown_pct.toFixed(2)+'% → '+esc(dw.level||''):'—'}</span>
        <b>Record</b><span>${o.n?o.wins+'W/'+o.losses+'L · PF '+(o.profit_factor??'—')+' · exp '+(o.expectancy_r!=null?o.expectancy_r.toFixed(2)+'R':'—'):'no decided paper trades yet'}</span>
        <b>Discipline</b><span>${j.n?j.violation_rate*100+'% violations ('+j.violations+'/'+j.n+')':'no journal entries yet'}</span></div>`;
    html+=`<div class="flex" style="margin-top:8px">${flagBtn('angry','😡 Angry')}${flagBtn('tired','😴 Tired')}${flagBtn('revenge','🔁 Revenge')}${flagBtn('chasing','🏃 Chasing')}
      <button class="rowbtn" onclick="setTraderState('all',0)">✕ Clear all</button></div>`;
    if(st.note) html+=`<div class="note" style="margin-top:4px">note: ${esc(st.note)}</div>`;
    if(closed) html+=`<div class="err" style="margin-top:6px">No new trades — ${g.blocked_by.map(esc).join('; ')}</div>`;
    html+=`<div class="note" style="margin-top:6px">Enforced at approval + paper runner. CLI: <code>python main.py risk</code> · <code>python main.py tradestate</code> · <code>python main.py journal</code></div>`;
    el.innerHTML=html;
  }catch(e){ el.innerHTML='<span class="err">'+esc(e)+'</span>'; }
}

async function setTraderState(key, val){
  try{
    await fetch('/api/trader-state',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(key==='all'?{angry:false,tired:false,revenge:false,chasing:false}:{[key]:!!val})});
  }catch(e){}
  loadRiskGate();
}

async function runPaper(){
  const m=document.getElementById('papermsg'); if(m) m.textContent='checking live candles…';
  try{
    const d=await (await fetch('/api/paper/run',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json();
    if(m) m.textContent=d.ok?`✓ checked ${d.run.checked||0}; entries ${d.run.opened||0}; closed ${d.run.closed||0}`:'error: '+(d.error||'');
  }catch(e){ if(m) m.textContent='error: '+e; }
  loadPaper(); loadHistory(); loadPending(); loadLearning();
}

async function loadHealth(){
  const el=document.getElementById('health'); if(!el) return;
  try{
    const d=await (await fetch('/api/health')).json();
    if(d.error){ el.innerHTML='<span class="err">'+esc(d.error)+'</span>'; return; }
    const dm=d.data||{}, db_=d.database||{}, gate=d.risk_gate||{}, prog=gate.progression||{},
          probes=Object.entries(dm.probe||{});
    el.innerHTML=`<div class="kv">
      <b>Mode</b><span>${esc(dm.mode||'?')}${dm.live?'':' <span class="note">(demo)</span>'}</span>
      <b>Database</b><span>${db_.ok?'ok':'<span class="err">FAIL</span>'} · ${db_.scans||0} scans · ${db_.backtest_samples||0} bt · ${db_.paper_samples||0} paper</span>
      <b>Risk gate</b><span class="${gate.allowed?'okc':'err'}">${gate.allowed?'OPEN':'CLOSED'}</span>
      <b>Progression</b><span>${esc(prog.level||'?')}</span>
      <b>Learning</b><span>${(d.learning||{}).calibration_entries||0} entries · ${((d.learning||{}).proven_setups||[]).length} proven</span>
      <b>MCP</b><span>${d.mcp&&d.mcp.available?'ready':'not installed'}</span>
      <b>LLM</b><span>${d.llm&&d.llm.enabled?'enabled':'off'}</span></div>
      ${probes.length?`<div class="note" style="margin-top:6px">data feeds: ${probes.map(([s,p])=>`${esc(s)} ${p.ok?'✓':'✗'}`).join(' · ')}</div>`:''}
      <div class="note" style="margin-top:4px">Same report as <code>python main.py agent health</code>.</div>`;
  }catch(e){ el.innerHTML='<span class="err">'+esc(e)+'</span>'; }
}

async function loadAgents(){
  const el=document.getElementById('agents'); if(!el) return;
  el.innerHTML='<span class="muted">building briefing…</span>';
  try{
    const d=await (await fetch('/api/agents')).json();
    if(d.error){ el.innerHTML='<span class="err">'+esc(d.error)+'</span>'; return; }
    const rows=(d.assets||[]).map(a=>{
      const side=a.desk_action||a.action||'';
      const mark=(side==='BUY'||side==='SELL')?(a.blocked_by&&a.blocked_by.length?'⚠':'✓'):'·';
      const conf=a.confidence_pct!=null?a.confidence_pct+'%':(a.confidence||'—');
      const vetoes=(a.blocked_by||[]).map(esc).join('; ');
      return `<div class="row"><span>${mark} <b>${esc(a.symbol)}</b> <span class="${cls(side)}">${side}</span> conf=${conf} ${a.entry?'@ '+fmt(a.entry):''}</span>
        <span class="note">${esc((a.reason||'').slice(0,60))}${vetoes?'<br><span class="err">✗ '+vetoes+'</span>':''}</span></div>`;
    }).join('');
    const gate=d.risk_gate||{}, prog=gate.progression||{};
    el.innerHTML=`${rows||'<span class="muted">no assets</span>'}
      <div class="note" style="margin-top:6px">Risk gate ${gate.allowed?'OPEN':'CLOSED'} · progression ${esc(prog.level||'?')} · ${d.pending_reviews||0} pending · ${(d.open_exposure||[]).length} open paper trade(s)</div>
      <div class="note" style="white-space:pre-wrap;margin-top:6px">${esc(d.narrative||'')}</div>
      <div class="note" style="margin-top:4px">CLI: <code>python main.py agent morning</code>.</div>`;
  }catch(e){ el.innerHTML='<span class="err">'+esc(e)+'</span>'; }
}

async function loadMcp(){
  const el=document.getElementById('mcp'); if(!el) return;
  try{
    const d=await (await fetch('/api/mcp')).json();
    if(d.error){ el.innerHTML='<span class="err">'+esc(d.error)+'</span>'; return; }
    el.innerHTML=`<div class="kv">
      <b>Server</b><span class="${d.available?'okc':'err'}">${d.available?'ready':'not installed'}</span>
      <b>Tools</b><span>${d.count||0}</span>
      <b>Transport</b><span>stdio (JSON-RPC 2.0)</span></div>
      <div class="note" style="margin-top:6px">${esc(d.note||'')}</div>
      <div class="note" style="margin-top:6px">${(d.tools||[]).map(t=>'<code>'+esc(t)+'</code>').join(' · ')}</div>`;
  }catch(e){ el.innerHTML='<span class="err">'+esc(e)+'</span>'; }
}

async function askDesk(){
  const q=document.getElementById('askq'); const out=document.getElementById('askout');
  const question=(q?q.value:'').trim(); if(!question) return;
  if(out) out.innerHTML='<span class="muted">thinking…</span>';
  try{
    const d=await (await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({question})})).json();
    const lines=(d.answer||[]);
    if(out) out.innerHTML=(lines.length?lines.map(l=>esc(l)).join('<br>'):esc(d.error||'no answer'))+
      (d.error?'<br><span class="err">'+esc(d.error)+'</span>':'');
  }catch(e){ if(out) out.innerHTML='<span class="err">'+esc(e)+'</span>'; }
}

async function loadHistory(){
  try{
    const d=await (await fetch('/api/history')).json();
    const el=document.getElementById('hist'); if(!el) return;
    const h=d.history||[];
    if(!h.length){ el.innerHTML='<span class="muted">No scans yet.</span>'; return; }
    el.innerHTML=h.map(x=>`<div class="row">
      <span onclick="openModal(${x.id})">#${x.id} <b>${x.symbol}</b> ${x.timeframe} <span class="${cls(x.action)}">${x.action}</span> ${x.confidence_label} @ ${fmt(x.entry)}</span>
      <span class="flex"><span class="badge" style="background:${stCls(x.status)}22;border-color:${stCls(x.status)}">${x.status}</span>
      ${x.status==='PENDING_REVIEW'?`<span class="btns"><button class="rowbtn ok" onclick="decide(${x.id},'APPROVED')">✓</button><button class="rowbtn no" onclick="decide(${x.id},'REJECTED')">✗</button></span>`:''}</span>
    </div>`).join('');
  }catch(e){ const el=document.getElementById('hist'); if(el) el.innerHTML='<span class="err">'+esc(e)+'</span>'; }
}

async function loadLearning(){
  try{
    const d=await (await fetch('/api/learning')).json();
    const el=document.getElementById('learn'); if(!el) return;
    const bt=d.backtest||{}, cal=d.calibration||{};
    let html=`<div class="flex" style="margin-bottom:8px">
      <button class="rowbtn" onclick="learnNow()">⚡ Learn now</button>
      <button class="rowbtn" onclick="backtestNow()">▶ Quick backtest + learn</button>
      <span id="learnmsg" class="note"></span></div>`;
    const o=bt.overall||{};
    const entries=Object.entries(cal||{});
    if(o.n){
      html+=`<div class="kv" style="margin-bottom:8px"><b>Graded</b><span>${o.n} plans</span>
        <b>Win-rate</b><span>${o.win_rate!=null?(o.win_rate*100).toFixed(1)+'%':'n/a'}</span>
        <b>Avg R</b><span>${o.avg_rr}</span><b>Wins/Losses</b><span>${o.wins}/${o.losses}</span></div>`;
      html+=`<table><tr><th>Plan type</th><th>n</th><th>Win%</th><th>AvgR</th></tr>`+
        (bt.by_type||[]).map(r=>`<tr><td>${esc(r.plan_type)}</td><td>${r.n}</td><td>${r.win_rate!=null?(r.win_rate*100).toFixed(0)+'%':'—'}</td><td>${r.avg_rr}</td></tr>`).join('')+`</table>`;
      if(o.n && !entries.length){
        html+=`<div class="note" style="margin-top:6px">Backtest data exists but is not applied yet — click <b>⚡ Learn now</b>.</div>`;
      }
    } else {
      html+=`<span class="muted">No backtest data yet. Click <b>▶ Quick backtest + learn</b> to grade the engine on recent data (~30s) and auto-apply the calibration.</span>`;
    }
    if(entries.length){
      html+=`<div style="margin-top:10px"><b>Calibration (applied to future signals)</b><table><tr><th>Plan</th><th>Mult</th><th>Exp R</th><th>Bt/Pp</th><th>Proven</th><th>TP R</th></tr>`+
        entries.map(([k,v])=>`<tr><td>${esc(k)}</td><td>${v.filtered?'<span class="err">FILTERED</span>':'×'+v.multiplier}</td><td>${v.expectancy!=null?v.expectancy.toFixed(2):'—'}</td><td>${v.backtest_samples||0}/${v.paper_samples||0}</td><td>${v.proven?'<span class="okc">✓ proven</span>':'<span class="note">unproven</span>'}</td><td>${v.tp_rr!=null?v.tp_rr:'—'}</td></tr>`).join('')+`</table></div>`;
    }
    const bm=d.metrics||{}, bo=bm.overall||{};
    if(bo.n){
      html+=`<div style="margin-top:10px"><b>Business scorecard (decided paper trades)</b><div class="kv">
        <b>Win rate</b><span>${(bo.win_rate*100).toFixed(1)}% (${bo.wins}W/${bo.losses}L)</span>
        <b>Expectancy</b><span>${bo.expectancy_r!=null?bo.expectancy_r.toFixed(3)+'R':'—'}</span>
        <b>Profit factor</b><span>${bo.profit_factor??'—'}</span>
        <b>Max drawdown</b><span>${bo.max_drawdown_pct!=null?bo.max_drawdown_pct.toFixed(2)+'%':'—'}</span>
        <b>Streaks</b><span>${bo.max_win_streak||0}W / ${bo.max_loss_streak||0}L</span>
        <b>Avg win/loss</b><span>${bo.avg_win_r!=null?bo.avg_win_r.toFixed(2)+'R':'—'} / ${bo.avg_loss_r!=null?bo.avg_loss_r.toFixed(2)+'R':'—'}</span></div></div>`;
    }
    const jj=d.journal||{};
    if(jj.n){
      html+=`<div class="note" style="margin-top:6px">Discipline: ${jj.violations}/${jj.n} trades (${(jj.violation_rate*100).toFixed(1)}%) violated the system — the goal is 0%.</div>`;
    }
    el.innerHTML=html;
  }catch(e){ const el=document.getElementById('learn'); if(el) el.innerHTML='<span class="err">'+esc(e)+'</span>'; }
}

async function learnNow(){
  const m=document.getElementById('learnmsg'); if(m) m.textContent='learning…';
  try{
    const d=await (await fetch('/api/learn',{method:'POST'})).json();
    if(m) m.textContent=d.ok?`✓ calibration saved (${Object.keys(d.profile||{}).length} setups)`:'error: '+(d.error||'');
  }catch(e){ if(m) m.textContent='error: '+e; }
  loadLearning(); load(true);
}

async function backtestNow(){
  const b=document.querySelector('button[onclick="backtestNow()"]');
  const m=document.getElementById('learnmsg');
  if(b){ b.disabled=true; b.textContent='running… up to ~30s'; }
  if(m) m.textContent='backtesting recent bars + learning…';
  try{
    const r=await fetch('/api/backtest',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({symbol:(document.getElementById('sym').value.trim().toUpperCase()||'BTCUSDT'),
                           tf:document.getElementById('tf').value, bars:300, step:3, horizons:'1,4,24'})});
    const d=await r.json();
    if(m) m.textContent=d.ok?`✓ graded ${d.saved} plans — calibration updated`:'error: '+(d.error||'');
  }catch(e){ if(m) m.textContent='error: '+e; }
  if(b){ b.disabled=false; b.textContent='▶ Quick backtest + learn'; }
  loadLearning(); load(true);
}

async function loadSources(){
  const el=document.getElementById('conn'); if(!el) return;
  try{
    const d=await (await fetch('/api/sources')).json();
    const badge=(on,label)=>`<span class="badge" style="background:${on?'rgba(34,197,94,.15)':'rgba(139,160,189,.12)'};color:${on?'var(--green)':'var(--mut)'};border-color:${on?'var(--green)':'transparent'}">${label} ${on?'●':'○'}</span>`;
    el.innerHTML=`<div class="flex">${badge(true,'Binance data')}${badge(d.cryptodada,'CryptoDada')}${badge(d.discord_read,'Discord read')}${badge(d.discord_webhook,'Discord push')}${badge(d.telegram,'Telegram')}${badge(d.llm,'LLM brain')}</div>
      <div class="note" style="margin-top:6px">○ = not configured — add to <code>.env</code> to enable. Everything else works with no keys.</div>`;
  }catch(e){ el.innerHTML='<span class="err">'+esc(e)+'</span>'; }
}

async function systemCheck(){
  const el=document.getElementById('diag'), m=document.getElementById('diagmsg');
  if(el) el.innerHTML=''; if(m) m.textContent='checking… (few seconds)';
  try{
    const d=await (await fetch('/api/diag')).json();
    const rows=Object.entries(d.sources||{}).map(([k,v])=>
      `<tr><td>${k}</td><td style="color:${v.ok?'var(--green)':'var(--red)'}">${v.ok?'✓ ok':'✗ fail'}</td><td>${v.ms}ms</td><td class="note">${esc(v.err||'')}</td></tr>`).join('');
    if(el) el.innerHTML=`<table><tr><th>Source</th><th>Status</th><th>Time</th><th>Error</th></tr>${rows}</table>`;
    if(m) m.textContent=d.all_ok?`✓ all ${d.total} sources reachable`:`${d.ok_count}/${d.total} sources reachable — the rest will degrade gracefully`;
  }catch(e){ if(m) m.textContent='error: '+e; }
}

async function decide(id, decision, note){
  const n=document.getElementById('note-'+id); const noteTxt=(n&&n.value)||'';
  try{
    await fetch('/api/review',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({scan_id:id,decision:decision,note:noteTxt})});
  }catch(e){}
  closeModal();
  // quiet refresh — no 'Scanning…' restart feel; just updates in place
  await load(true, true);
  loadPending(); loadPaper(); loadHistory(); loadLearning();
}

async function openModal(id){
  try{
    const d=await (await fetch('/api/signal?id='+id)).json();
    const s=d.scan||{}, plans=(Array.isArray(d.plans)?d.plans:[]), decs=(Array.isArray(d.decisions)?d.decisions:[]), paper=d.paper_trade||{};
    const tps=p=>{ try{ return (p.take_profits||[]).map(fmt).join(', '); }catch(e){ return '—'; } };
    document.getElementById('mbody').innerHTML=`
      <div class="flex" style="justify-content:space-between"><h2 style="margin:0">Signal #${s.id} — ${esc(s.symbol||'')} ${esc(s.timeframe||'')} <span class="pill ${cls(s.action)}">${esc(s.action||'')}</span></h2>
      <button class="rowbtn" onclick="closeModal()">✕</button></div>
      <div class="kv" style="margin-top:12px">
        <b>Status</b><span>${esc(s.status||'—')}</span><b>Confidence</b><span>${esc(s.confidence_label||'—')}</span>
        <b>Entry</b><span class="mono">${fmt(s.entry)}</span><b>SL</b><span class="mono">${fmt(s.stop_loss)}</span>
        <b>TP</b><span class="mono">${fmt(s.take_profit)}</span><b>RR</b><span>${s.risk_reward??'—'}</span>
        <b>Created</b><span>${esc(s.created_at||'—')}</span><b>Reason</b><span>${esc(s.reason||'')}</span>
      </div>
      ${paper.id?`<h3 style="margin:14px 0 6px">Paper-trade monitor</h3><div class="kv">
        <b>Status</b><span>${esc(paper.status||'—')}</span><b>Setup</b><span>${esc(paper.plan_type||'Signal')}</span>
        <b>Outcome</b><span>${esc(paper.outcome||'—')}</span><b>R achieved</b><span>${paper.rr_achieved!=null?paper.rr_achieved+'R':'—'}</span>
        <b>MAE / MFE</b><span class="mono">${paper.mae!=null?fmt(paper.mae):'—'} / ${paper.mfe!=null?fmt(paper.mfe):'—'}</span>
        <b>Regime</b><span>${esc(paper.regime||'—')}</span>
        <b>Last price</b><span class="mono">${fmt(paper.last_price)}</span><b>Note</b><span>${esc(paper.close_reason||'')}</span>
      </div>`:''}
      ${d.journal?`<h3 style="margin:14px 0 6px">Journal (post-trade)</h3><div class="kv">
        <b>Followed rules</b><span>${d.journal.followed_rules==null?'—':d.journal.followed_rules?'<span class="okc">YES</span>':'<span class="err">NO</span>'}</span>
        <b>Emotion</b><span>${esc(d.journal.emotion||'—')}</span><b>Mistake</b><span>${esc(d.journal.mistake||'—')}</span>
        <b>Would change</b><span>${esc(d.journal.would_change||'—')}</span><b>Notes</b><span>${esc(d.journal.notes||'—')}</span>
      </div><div class="note" style="margin-top:4px">Record/update: <code>python main.py journal ${s.id} --followed-rules 1 --emotion calm ...</code></div>`:''}
      <h3 style="margin:14px 0 6px">Lifecycle trail</h3>
      ${decs.length?decs.map(x=>`<div class="muted">${esc(x.from_state||'')} → <b>${esc(x.to_state||'')}</b> by ${esc(x.reviewer||'')} <span class="note">${esc(x.note||'')}</span></div>`).join(''):'<span class="muted">no decisions yet</span>'}
      <h3 style="margin:14px 0 6px">Plans</h3>
      ${plans.length?plans.map(p=>`<div class="plan"><div class="h"><b>${esc(p.type||'')}</b><span class="badge">${p.confidence??''}%</span></div><div class="c">${esc(p.condition||'')}</div><div class="c mono">entry ${fmt(p.entry)} · sl ${fmt(p.stop_loss)} · tp ${tps(p)} · RR ${p.risk_reward??'—'}</div></div>`).join(''):'<span class="muted">none</span>'}
      <div class="flex" style="margin-top:12px">
        ${s.status==='PENDING_REVIEW'?`<button class="rowbtn ok" onclick="decide(${s.id},'APPROVED')">✓ Approve</button><button class="rowbtn no" onclick="decide(${s.id},'REJECTED')">✗ Reject</button>`:''}
        ${s.status==='APPROVED'?`<button class="rowbtn" onclick="decide(${s.id},'EXECUTED')">▶ Executed</button><button class="rowbtn no" onclick="decide(${s.id},'SKIPPED')">Skip</button>`:''}
        ${s.status==='EXECUTED'?`<button class="rowbtn" style="background:var(--amber)" onclick="decide(${s.id},'CLOSED')">✔ Close</button>`:''}
      </div>`;
    document.getElementById('modal').style.display='block';
  }catch(e){ document.getElementById('mbody').innerHTML='<div class="err">Error opening signal: '+esc(e)+'</div>'; }
}
function closeModal(){ document.getElementById('modal').style.display='none'; }
document.getElementById('modal').addEventListener('click', e=>{ if(e.target.id==='modal') closeModal(); });

async function coach(){
  const el=document.getElementById('coach'); if(!el) return;
  el.textContent='thinking…';
  const sym=document.getElementById('sym').value.trim().toUpperCase()||'BTCUSDT';
  const tf=document.getElementById('tf').value;
  try{
    const d=await (await fetch(`/api/coach?symbol=${sym}&tf=${tf}`)).json();
    const nl=String.fromCharCode(10);
    el.textContent=(d.explain||[]).join(nl)+nl+nl+d.mentor+nl+nl+'📈 YOUR FEEDBACK'+nl+(d.feedback||[]).join(nl);
  }catch(e){ el.textContent='Error: '+e; }
}

load(true);
setInterval(()=>{ if(document.getElementById('auto').checked) load(false, true); }, 30000);
</script></body></html>
"""


def make_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template_string(HTML, symbol=SYMBOL, tf=TIMEFRAME,
                                      version=VERSION, symbols=symbol_choices(SYMBOLS))

    @app.get("/api/scan")
    def api_scan():
        symbol = _sym(request.args.get("symbol", SYMBOL))
        tf = request.args.get("tf", TIMEFRAME)
        force = request.args.get("force") == "1"
        try:
            if force:
                _CACHE.update(payload=None, ts=0, ttl=_CACHE.get("ttl", 40))
            return jsonify(compute_payload(symbol, tf, save=True, use_cache=not force))
        except ConnectionError as exc:
            return jsonify({"error": str(exc)}), 502
        except Exception as exc:  # pragma: no cover
            return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500

    @app.get("/api/intelligence")
    def api_intelligence():
        """JSON-only professional desk report for an asset/timeframe."""
        symbol = _sym(request.args.get("symbol", SYMBOL))
        tf = request.args.get("tf", TIMEFRAME)
        force = request.args.get("force") == "1"
        try:
            payload = compute_payload(symbol, tf, save=False, use_cache=not force)
            return jsonify(payload.get("intelligence", {}))
        except Exception as exc:
            return jsonify({
                "asset": symbol,
                "signal": "NO TRADE",
                "confidence": 0,
                "reason": [f"Insufficient market data: {type(exc).__name__}: {exc}"],
            }), 200

    @app.get("/api/pending")
    def api_pending():
        from data.database import SignalDB
        with SignalDB() as db:
            return jsonify({"pending": db.pending_reviews()})

    @app.get("/api/paper")
    def api_paper():
        """Paper-monitor dashboard state. Read-only; does not start a runner."""
        symbol = _sym(request.args.get("symbol")) if request.args.get("symbol") else None
        from data.database import SignalDB
        with SignalDB() as db:
            return jsonify({"stats": db.paper_trade_stats(symbol)})

    @app.post("/api/paper/run")
    def api_paper_run():
        """One explicit, safe runner pass for the dashboard button.

        Continuous unattended monitoring belongs to ``python main.py paper
        --watch`` so it survives a browser tab closing.  This endpoint never
        sends an order to an exchange.
        """
        body = request.get_json(silent=True) or {}
        symbol = _sym(body.get("symbol")) if body.get("symbol") else None
        try:
            from data.database import SignalDB
            from data.paper_trading import PaperTradingRunner
            with SignalDB() as db:
                run = PaperTradingRunner(db=db, client=maybe_client()).run_once(symbol=symbol).as_dict()
            return jsonify({"ok": True, "run": run})
        except Exception as exc:
            return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500

    @app.post("/api/review")
    def api_review():
        body = request.get_json(silent=True) or {}
        scan_id = body.get("scan_id")
        decision = (body.get("decision") or "").upper()
        note = body.get("note", "")
        force = bool(body.get("force"))
        if decision not in ("APPROVED", "REJECTED", "EXECUTED", "CLOSED", "SKIPPED"):
            return jsonify({"error": f"bad decision {decision}"}), 400
        from data.database import SignalDB
        from engine.lifecycle import LifecycleError
        from brain.risk_gate import evaluate as gate_evaluate
        from data.paper_trading import _primary_plan
        with SignalDB() as db:
            # Enforced risk & discipline gate (decisions B6/B7/B9/B10).
            if decision == "APPROVED" and not force:
                scan = db.get_scan(int(scan_id)) if str(scan_id).isdigit() else None
                if scan is None:
                    return jsonify({"error": f"scan #{scan_id} not found"}), 404
                plan_type = _primary_plan(scan).get("type")
                gate = gate_evaluate(db, symbol=scan.get("symbol"),
                                     plan_type=plan_type, action=scan.get("action"))
                if not gate["allowed"]:
                    return jsonify({
                        "error": "Risk gate CLOSED — " + "; ".join(gate["blocked_by"]),
                        "blocked_by": gate["blocked_by"],
                        "force_available": True,
                    }), 409
            try:
                new = db.update_status(int(scan_id), decision, note=note,
                                       reviewer="dashboard")
            except (LifecycleError, TypeError, ValueError) as exc:
                return jsonify({"error": str(exc)}), 400
            if new is None:
                return jsonify({"error": f"scan #{scan_id} not found"}), 404
        return jsonify({"ok": True, "scan_id": scan_id, "status": new})

    @app.get("/api/risk")
    def api_risk():
        """Risk & discipline gate status (decisions B4/B5/B6/B7/B9)."""
        from data.database import SignalDB
        from brain.risk_gate import evaluate as gate_evaluate, status_text
        from brain.metrics import business_metrics
        from brain.journal import violation_rate
        try:
            with SignalDB() as db:
                gate = gate_evaluate(db)
                metrics = business_metrics(db)
                journal = violation_rate(db)
                state = db.get_trader_state()
            return jsonify({
                "gate": gate,
                "trader_state": state,
                "metrics": metrics,
                "journal": journal,
                "status_text": status_text.__doc__ or "",
            })
        except Exception as exc:
            return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500

    @app.post("/api/trader-state")
    def api_trader_state():
        """Set/clear behavioral no-trade flags (decision B7)."""
        body = request.get_json(silent=True) or {}
        from data.database import SignalDB
        with SignalDB() as db:
            state = db.set_trader_state(
                angry=body.get("angry"), tired=body.get("tired"),
                revenge=body.get("revenge"), chasing=body.get("chasing"),
                note=body.get("note", ""))
        return jsonify({"ok": True, "state": state})

    @app.get("/api/history")
    def api_history():
        from data.database import SignalDB
        with SignalDB() as db:
            return jsonify({"history": db.latest_scans(limit=15)})

    @app.get("/api/signal")
    def api_signal():
        scan_id = request.args.get("id", type=int)
        from data.database import SignalDB
        with SignalDB() as db:
            scan = db.get_scan(scan_id)
            if scan is None:
                return jsonify({"error": "not found"}), 404
            plans = json.loads(scan.get("plans_json") or "[]")
            decisions = db.decision_history(scan_id)
            paper_trade = db.paper_trade_for_scan(scan_id)
            journal = db.get_journal(scan_id)
        return jsonify({"scan": scan, "plans": plans, "decisions": decisions,
                        "paper_trade": paper_trade, "journal": journal})

    @app.get("/api/learning")
    def api_learning():
        from data.database import SignalDB
        try:
            with SignalDB() as db:
                from brain.metrics import business_metrics
                from brain.journal import violation_rate
                return jsonify({
                    "backtest": db.backtest_stats(),
                    "calibration": db.load_calibration(),
                    "plan_stats": db.plan_stats(),
                    "metrics": business_metrics(db),
                    "journal": violation_rate(db),
                })
        except Exception as exc:  # never 500 — the card degrades gracefully
            return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}",
                            "backtest": {"overall": {"n": 0}},
                            "calibration": {}, "plan_stats": []})

    @app.get("/api/coach")
    def api_coach():
        symbol = _sym(request.args.get("symbol", SYMBOL))
        tf = request.args.get("tf", TIMEFRAME)
        from brain.coach import explain_signal, mentor, personal_feedback
        from data.database import SignalDB
        payload = compute_payload(symbol, tf, save=False, use_cache=False)
        with SignalDB() as db:
            feedback = personal_feedback(db)
        return jsonify({
            "explain": explain_signal(payload),
            "mentor": mentor(payload),
            "feedback": feedback,
        })

    @app.get("/api/candles")
    def api_candles():
        """OHLCV for the inline candlestick chart (no external libs)."""
        symbol = _sym(request.args.get("symbol", SYMBOL))
        tf = request.args.get("tf", TIMEFRAME)
        limit = min(int(request.args.get("limit", 90)), 300)
        try:
            df = maybe_client().klines(symbol, tf, limit)
            return jsonify({"candles": df.to_dict("records"),
                            "symbol": symbol, "tf": tf})
        except ConnectionError as exc:
            return jsonify({"error": str(exc)}), 502

    @app.get("/api/state")
    def api_state():
        symbol = _sym(request.args.get("symbol", SYMBOL))
        tf = request.args.get("tf", TIMEFRAME)
        from brain.state_memory import SignalMemory
        mem = SignalMemory()
        return jsonify({"state": mem.get_state(symbol, tf),
                        "events": mem.history(symbol, tf, limit=15)})

    @app.post("/api/learn")
    def api_learn():
        """One-click: recompute the calibration profile from stored backtests."""
        from brain.calibrator import learn
        try:
            res = learn()
            return jsonify({"ok": True, **res})
        except Exception as exc:  # pragma: no cover
            return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500

    @app.post("/api/backtest")
    def api_backtest():
        """One-click: quick backtest on recent bars + auto-learn."""
        body = request.get_json(silent=True) or {}
        symbol = _sym(body.get("symbol", SYMBOL))
        tf = body.get("tf", TIMEFRAME)
        bars = min(int(body.get("bars", 300)), 1000)
        step = int(body.get("step", 3))
        horizons = [float(h) for h in str(body.get("horizons", "1,4,24")).split(",") if h]
        try:
            from brain.calibrator import learn
            from data.backtester import run_backtest
            from data.database import SignalDB
            df = maybe_client().klines(symbol, tf, bars)
            res = run_backtest(df, symbol=symbol, timeframe=tf,
                               horizons=horizons, step=step,
                               min_confidence=MIN_CONFIDENCE)
            run_id = time.strftime("%Y%m%d_%H%M%S")
            rows = []
            for g in res["graded"]:
                r = g.as_row()
                r.update({"run_id": run_id, "symbol": symbol, "timeframe": tf})
                rows.append(r)
            with SignalDB() as db:
                n = db.save_backtest_rows(rows, run_id)
            profile = learn()
            return jsonify({"ok": True, "saved": n, "report": res["report"],
                            "calibration": profile["profile"]})
        except Exception as exc:
            return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500

    @app.get("/api/sources")
    def api_sources():
        """Configuration status of optional external connections."""
        from config import (CRYPTODADA_BASE_URL, DISCORD_TOKEN, DISCORD_CHANNEL_IDS,
                            DISCORD_ANNOUNCE_WEBHOOK, TELEGRAM_BOT_TOKEN,
                            TELEGRAM_CHAT_ID, LLM_PROVIDER)
        return jsonify({
            "cryptodada": bool(CRYPTODADA_BASE_URL and "YOUR-CRYPTODADA" not in CRYPTODADA_BASE_URL),
            "discord_read": bool(DISCORD_TOKEN and DISCORD_CHANNEL_IDS),
            "discord_webhook": bool(DISCORD_ANNOUNCE_WEBHOOK),
            "telegram": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
            "llm": LLM_PROVIDER != "off",
        })

    @app.get("/api/diag")
    def api_diag():
        """Connectivity check: which external sources are reachable, with
        response times — powers the dashboard 'System check' button."""
        import requests as _req
        from data.binance_client import BINANCE_HOSTS, BINANCE_FUTURES_HOSTS
        out: dict = {}

        def probe(name, fn):
            t0 = time.time()
            try:
                ok = fn()
                out[name] = {"ok": bool(ok), "ms": int((time.time() - t0) * 1000)}
            except Exception as exc:
                out[name] = {"ok": False, "ms": int((time.time() - t0) * 1000),
                             "err": str(exc)[:120]}

        def spot():
            r = _req.get(f"{BINANCE_HOSTS[0]}/api/v3/ping", timeout=4)
            return r.status_code == 200

        def fapi():
            r = _req.get(f"{BINANCE_FUTURES_HOSTS[0]}/fapi/v1/ping", timeout=4)
            return r.status_code == 200

        def fng():
            r = _req.get("https://api.alternative.me/fng/?limit=1", timeout=4)
            return r.status_code == 200

        def cg():
            r = _req.get("https://api.coingecko.com/api/v3/global", timeout=4,
                         headers={"User-Agent": "CryptoBrain/1.0"})
            return r.status_code == 200

        def stooq():
            r = _req.get("https://stooq.com/q/l/?s=^spx&f=sd2t2ohlcv&h&e=csv",
                         timeout=4, headers={"User-Agent": "Mozilla/5.0"})
            return r.status_code == 200

        def news():
            r = _req.get("https://cointelegraph.com/rss", timeout=4,
                         headers={"User-Agent": "CryptoBrain/1.0"})
            return r.status_code == 200

        from concurrent.futures import ThreadPoolExecutor
        probes = {"binance_spot": spot, "binance_futures": fapi, "fear_greed": fng,
                  "coingecko": cg, "stooq_equities": stooq, "news_rss": news}
        with ThreadPoolExecutor(max_workers=len(probes)) as ex:
            futs = {ex.submit(probe, n, f): n for n, f in probes.items()}
            for fut in futs:
                fut.result()
        ok_count = sum(1 for v in out.values() if v.get("ok"))
        return jsonify({"sources": out, "ok_count": ok_count, "total": len(out),
                        "all_ok": ok_count == len(out)})

    @app.get("/api/health")
    def health():
        """Full system health — same report as `python main.py agent health`."""
        try:
            from brain.agent import health_report
            return jsonify(health_report())
        except Exception as exc:  # never 500 — degrade gracefully
            return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    @app.get("/api/agents")
    def api_agents():
        """Desk morning briefing: watchlist + gate + queue + narrative."""
        try:
            from brain.agent import morning_briefing
            return jsonify(morning_briefing(timeframe=request.args.get("tf", TIMEFRAME)))
        except Exception as exc:  # never 500 — degrade gracefully
            return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    @app.get("/api/mcp")
    def api_mcp():
        """MCP server status + tool inventory for the dashboard card."""
        try:
            from ai.mcp_server import TOOLS
            try:
                import mcp  # noqa: F401
                available = True
                note = "run `python main.py mcp` and point Claude Desktop / Cursor at it"
            except Exception:
                available = False
                note = "mcp package not installed — pip install mcp"
            return jsonify({"available": available, "note": note,
                            "count": len(TOOLS),
                            "tools": [t["name"] for t in TOOLS]})
        except Exception as exc:
            return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    @app.post("/api/ask")
    def api_ask():
        """Natural-language question to the desk (intent-based, offline-capable)."""
        body = request.get_json(silent=True) or {}
        question = str(body.get("question", "")).strip()
        if not question:
            return jsonify({"ok": False, "error": "empty question"}), 400
        try:
            from brain.agent import ask
            return jsonify(ask(question, symbol=_sym(body.get("symbol")),
                               timeframe=body.get("tf", TIMEFRAME)))
        except Exception as exc:
            return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500

    return app


def serve(app: Flask, host: str = DASHBOARD_HOST, port: int = DASHBOARD_PORT) -> None:
    app.run(host=host, port=port, debug=False, threaded=True)
