"""Combined Regime Reclaim V2 challenger: forward-only, event-data research.

V1 is intentionally imported without modification. All V2 policy parameters were
declared before looking at any post-freeze events. No live/paper worker is changed.
Inputs: genuinely new, independently observed SPY minute OHLCV/volume-ratio/RV
and 0DTE event-time NBBO/size/IV per frozen_regime_forward.py.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from .frozen_regime_forward import (
    FROZEN_COMMIT, FROZEN_POLICY, START_CASH, TRAIL_PCT, HARD_STOP_PCT,
    MAX_HOLD_MIN, FEE_STRESS_PER_TRADE, _extra, REQUIRED_BAR_EXTRA,
    REQUIRED_QUOTE_EXTRA, session_plan, replay_session, latest_legacy_day,
)
from .regime_reclaim_frozen_signal import identify_setups, ema, simulate
from .weekly_options_data import load_session, available_sessions, audit

V2_POLICY = "regime_router_vwap_candle_cost_rank_adaptive20_v1base"
V2_VERSION = "2026-10-12-predeclared-v1"
FEE_PER_SIDE = 0.65
SLIP_PER_SHARE = 0.01
MAX_SPREAD = 0.035
MAX_IV_TO_RV = 1.5


def session_vwap(bars, count: int) -> float:
    """Session VWAP through count ALREADY completed bars only."""
    past = bars[:count]
    volume = sum(b.volume for b in past)
    if volume <= 0:
        raise ValueError("zero available volume for as-of VWAP")
    return sum(b.typical * b.volume for b in past) / volume


def route_candidate(signal: dict, bars, i: int) -> dict:
    """Causal regime router + confirmation at completion of bar i.

    Only previously frozen V1 trend/stretch hypotheses are eligible; we do
    not opportunistically introduce breakout/failed-range arms.
    """
    out = dict(signal)
    out["selected"] = False
    if signal["setup"] not in ("trend_reclaim", "stretch_reclaim"):
        out["reason"] = "NON_FROZEN_SETUP"
        return out
    if i < 35 or bars[i].available_at != signal["at"]:
        out["reason"] = "SIGNAL_BAR_UNAVAILABLE"
        return out
    bar = bars[i]
    prev = bars[i-1]
    prev2 = bars[i-2]
    slope = float(signal["trend_bps"])
    efficiency = float(signal["efficiency"])
    if abs(slope) >= 2.0 and efficiency >= .25:
        regime = "trend_up" if slope > 0 else "trend_down"
    elif abs(slope) <= 6.0 and efficiency < .45:
        regime = "range"
    else:
        regime = "uncertain"
    out["regime"] = regime
    setup = signal["setup"]
    side = signal["side"]
    if setup == "trend_reclaim":
        allowed = "trend_up" if side == "call" else "trend_down"
        if regime != allowed:
            out["reason"] = "ROUTER_REJECTED_TREND"
            return out
    elif regime != "range":
        out["reason"] = "ROUTER_REJECTED_STRETCH"
        return out

    # The entire candle is already closed at signal time. Body-strength and
    # close-location test reduce wick-only reclaims.
    width = bar.high - bar.low
    directional_body = (bar.close - bar.open) if side == "call" else (bar.open - bar.close)
    loc = (bar.close-bar.low)/width if width > 0 else .5
    rvol_min = .90 if setup == "trend_reclaim" else .85
    if width <= 0 or directional_body/width < (.20 if setup == "trend_reclaim" else .25):
        out["reason"] = "WEAK_REVERSAL_CANDLE"
        return out
    if (side == "call" and loc < .60) or (side == "put" and loc > .40):
        out["reason"] = "WEAK_CLOSE_LOCATION"
        return out
    if float(signal["volume_ratio"]) < rvol_min:
        out["reason"] = "INSUFFICIENT_CONFIRMATION_RVOL"
        return out

    now_vwap = session_vwap(bars, i+1)
    prior_vwap = session_vwap(bars, i)
    out["vwap_asof"] = round(now_vwap, 5)
    if setup == "trend_reclaim":
        if (side == "call" and bar.close < now_vwap) or (side == "put" and bar.close > now_vwap):
            out["reason"] = "AGAINST_SESSION_VWAP"
            return out
    else:
        # Previously stretched in opposite direction, now reversing TOWARD VWAP.
        prev_move = prev.close - prev2.close
        exhaustion = prev_move < 0 if side == "call" else prev_move > 0
        converging = abs(bar.close-now_vwap) < abs(prev.close-prior_vwap)
        if not exhaustion or not converging:
            out["reason"] = "NO_VWAP_CONVERGENCE_OR_EXHAUSTION"
            return out
    out["selected"] = True
    out["reason"] = "PASS_V2_REGIME_AND_PRICE_CONFIRMATION"
    return out


def session_plan_v2(root: Path, day) -> tuple[list[dict], list[dict], dict]:
    """Same exact session/event feed as V1; different precommitted decision."""
    session = load_session(root, day)
    addons = _extra(root, f"spy_bars_{day}", "ts_start", REQUIRED_BAR_EXTRA)
    option_addons = _extra(root, f"spy_option_quotes_{day}", "observed_at",
                           REQUIRED_QUOTE_EXTRA, "symbol")
    frames = []
    for bar in session.bars:
        extra = addons[bar.start]
        frames.append(SimpleNamespace(
            timestamp=bar.start,
            market=SimpleNamespace(spot=bar.close,
                volume_ratio=extra["volume_ratio"],
                realized_volatility=extra["realized_volatility"]),
        ))
    candidates = identify_setups(frames)
    at_to_index = {bar.available_at: i for i, bar in enumerate(session.bars)}
    decisions = []
    possible = []
    for event in candidates:
        i = at_to_index.get(event["at"])
        if i is None:
            decisions.append({**event, "selected": False, "reason": "NO_COMPLETED_BAR"})
            continue
        routed = route_candidate(event, session.bars, i)
        if not routed["selected"]:
            decisions.append(routed)
            continue
        signal_at = event["at"]
        lo, hi = signal_at + timedelta(minutes=1), signal_at + timedelta(minutes=2)
        eligible = [q for q in session.quotes
                    if lo <= q.observed_at <= hi and q.right == event["side"]
                    and q.ask > q.bid > 0 and .40 <= abs(q.delta) <= .60
                    and q.bid_size >= 1 and q.ask_size >= 1]
        first = min((q.observed_at for q in eligible), default=None)
        if first is None:
            decisions.append({**routed, "selected": False,
                              "reason": "NO_VALID_FRESH_EVENT_QUOTE"})
            continue
        # No looking at later timestamps if the first quote is too expensive.
        observed = [q for q in eligible if q.observed_at == first]
        realized = addons[session.bars[i].start]["realized_volatility"]
        if realized <= 0:
            decisions.append({**routed, "selected": False,
                              "reason": "NO_ASOF_SPY_REALIZED_VOL"})
            continue
        qualified = []
        for q in observed:
            mid = (q.bid + q.ask)/2
            spread = (q.ask-q.bid)/mid
            iv = option_addons[(q.observed_at,q.symbol)]["implied_volatility"]
            if spread > MAX_SPREAD or iv/realized > MAX_IV_TO_RV:
                continue
            # Regime-specific delta preference is a deterministic liquidity
            # heuristic, NOT a calibrated expected-return or option forecast.
            target = .55 if event["setup"] == "trend_reclaim" else .45
            depth = min(q.bid_size, q.ask_size)
            score = 10*spread + .6*abs(abs(q.delta)-target) + .08/math.sqrt(depth)
            qualified.append((score, q.symbol, q, spread, iv))
        if not qualified:
            decisions.append({**routed, "selected": False,
                              "quote_observed_at": first.isoformat(),
                              "reason": "FAIL_V1_SPREAD_OR_IV_COST_GATE"})
            continue
        _, _, chosen, spread, iv = min(qualified, key=lambda x:(x[0], x[1]))
        decisions.append({
            **routed, "selected": True, "reason": "PASS_V2_EXECUTION_GATE",
            "option": chosen.symbol, "quote_observed_at": first.isoformat(),
            "spread_fraction": round(spread, 7),
            "option_iv": iv, "spy_realized_volatility": realized,
            "option_delta": chosen.delta,
        })
        possible.append({
            "signal": event, "option": chosen,
            "debit": round((chosen.ask + SLIP_PER_SHARE)*100 + FEE_PER_SIDE, 2),
        })
    return decisions, possible, {
        "loaded_completed_ohlcv_bars": len(session.bars),
        "loaded_event_nbbo_options": len(session.quotes),
        "asof_scanned_signals": len(candidates),
    }


def deteriorating_structure(bars, quote_time, side: str) -> bool:
    """Use only completed bars available when this option bid was observed."""
    asof = [b for b in bars if b.available_at <= quote_time]
    if len(asof) < 36:
        return False
    closes = [b.close for b in asof]
    last_ema = ema(closes[-35:], 8)
    prior_ema = ema(closes[-36:-1], 8)
    if side == "call":
        return closes[-1] < last_ema and closes[-2] < prior_ema and closes[-1] < closes[-2]
    if side == "put":
        return closes[-1] > last_ema and closes[-2] > prior_ema and closes[-1] > closes[-2]
    raise ValueError("invalid side")


def adaptive_exit(ask, path, entry_at, side, bars):
    """30% base trail, permanently tighten to 20% on *observed* deterioration.

    Still applies the original 35% hard loss, 10-minute cap, spread/slippage
    model and full-position exit. No future SPY bars can influence early exits.
    """
    if not path:
        raise ValueError("empty exit path")
    debit = (ask+SLIP_PER_SHARE)*100 + FEE_PER_SIDE
    peak = debit
    trail = TRAIL_PCT/100
    for n,(at,bid) in enumerate(path):
        received = max(0.0, (bid-SLIP_PER_SHARE)*100 - FEE_PER_SIDE)
        peak = max(peak, received)
        if at >= entry_at + timedelta(minutes=2) and deteriorating_structure(bars,at,side):
            trail = min(trail, .20)
        if received <= debit*(1-HARD_STOP_PCT/100)+1e-9:
            reason = "independent_hard_stop_35pct"
        elif received <= peak*(1-trail)+1e-9:
            reason = "adaptive_trailing_20pct" if trail < TRAIL_PCT/100 else "trailing_stop"
        elif n == len(path)-1:
            reason = "hard_max_hold_10min"
        else:
            continue
        return {
            "pnl_usd": round(received-debit,6),
            "return_on_premium_pct": round((received/debit-1)*100,6),
            "exit_reason": reason, "exit_time": at.isoformat(),
        }
    raise RuntimeError("no exit")


def replay_v2(possible, session, settled: float, adaptive: bool):
    """Matches V1 one-position and T+1 rules; all entries decided before exits."""
    pending, flat = 0.0, None
    rows, decisions = [], []
    for item in sorted(possible, key=lambda c: (
            c["option"].observed_at, c["signal"]["at"], c["option"].symbol)):
        q = item["option"]
        entry_at, debit = q.observed_at, item["debit"]
        if flat is not None and entry_at <= flat:
            decisions.append({"reason": "ONE_POSITION_BUSY", "entry_at": entry_at.isoformat()})
            continue
        if debit > settled+1e-9:
            decisions.append({"reason": "INSUFFICIENT_T1_SETTLED_CASH",
                              "entry_at": entry_at.isoformat(), "required_usd": debit})
            continue
        settled -= debit
        due = entry_at + timedelta(minutes=MAX_HOLD_MIN)
        path = [(x.observed_at,x.bid) for x in session.quotes if
                x.symbol == q.symbol and entry_at < x.observed_at <= due
                and x.bid >= 0 and x.ask >= x.bid]
        if not path or due-path[-1][0] > timedelta(seconds=30):
            rows.append({"entry_at":entry_at.isoformat(), "symbol":q.symbol,
                         "reason":"UNSCOREABLE_EXIT_EVENT_DATA",
                         "entry_debit_usd":debit, "pnl_usd":None})
            flat=due
            continue
        paper = (adaptive_exit(q.ask,path,entry_at,q.right,session.bars)
                 if adaptive else simulate(q.ask,path,TRAIL_PCT))
        pnl=paper["pnl_usd"]
        pending += max(0.,debit+pnl)
        flat = __import__("datetime").datetime.fromisoformat(paper["exit_time"])
        rows.append({
            "entry_at":entry_at.isoformat(), "signal_at":item["signal"]["at"].isoformat(),
            "symbol":q.symbol, "side":q.right, "setup":item["signal"]["setup"],
            "entry_debit_usd":debit, "exit_at":paper["exit_time"],
            "exit_reason":paper["exit_reason"], "pnl_usd":pnl,
            "after_extra_2usd_friction_usd":round(pnl-FEE_STRESS_PER_TRADE,2),
        })
    return rows, settled, pending, decisions


def metrics(rows, settled, pending, equity_by_day):
    good = [x for x in rows if x.get("pnl_usd") is not None]
    profits=[x["pnl_usd"] for x in good if x["pnl_usd"]>0]
    losses=[x["pnl_usd"] for x in good if x["pnl_usd"]<0]
    high=START_CASH
    drawdown=0.0
    for x in equity_by_day:
        high=max(high,x)
        drawdown=max(drawdown,high-x)
    n=len(good)
    return {
        "paper_positions":len(rows), "scorable_positions":n,
        "unscorable_positions":len(rows)-n,
        "win_rate_pct":round(100*len(profits)/n,2) if n else None,
        "avg_pnl_usd":round(sum(x["pnl_usd"] for x in good)/n,2) if n else None,
        "profit_factor":round(sum(profits)/-sum(losses),3) if losses else None,
        "modeled_pnl_usd":round(sum(x["pnl_usd"] for x in good),2),
        "modeled_pnl_less_2_usd_per_trade":round(sum(x["pnl_usd"] for x in good)-2*n,2),
        "hypothetical_equity_usd":round(settled+pending,2),
        "max_modeled_end_of_day_drawdown_usd":round(drawdown,2),
        "note": "Not broker fills; unscored trades imply incomplete P&L." if len(rows)!=n else "Not broker fills.",
    }


def _csv(path, rows, minimum):
    columns=sorted(set(minimum)|{key for row in rows for key in row})
    with path.open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=columns)
        w.writeheader()
        w.writerows(rows)


def run_compare(event_dir: Path, legacy_dir: Path, output_dir: Path) -> dict:
    """Same unseen days and event files for frozen V1 and V2 (two exit arms)."""
    cutoff = latest_legacy_day(legacy_dir)
    sessions = available_sessions(event_dir)
    fresh = [x for x in sessions if x>cutoff]
    names = ("v1_frozen", "v2_router_fixed_exit", "v2_router_adaptive_exit")
    ledgers = {name: {"cash":START_CASH, "pending":0.0,
                      "equity":[], "trades":[], "decisions":[]} for name in names}
    skipped, processed = {}, []
    for day in fresh:
        try:
            decisions_v1, possible_v1, stats_v1 = session_plan(event_dir,day)
            decisions_v2, possible_v2, stats_v2 = session_plan_v2(event_dir,day)
            session = load_session(event_dir,day)
            if len(session.bars) < 36 or not session.quotes:
                raise ValueError("incomplete forward feed: need at least 36 completed bars and real event-observed option quotes")
        except (ValueError, FileNotFoundError, KeyError) as exc:
            skipped[str(day)] = str(exc)
            continue
        decisions = {"v1_frozen": decisions_v1,
                     "v2_router_fixed_exit": decisions_v2,
                     "v2_router_adaptive_exit": decisions_v2}
        for name in names:
            ledger=ledgers[name]
            ledger["cash"] += ledger["pending"]
            ledger["pending"] = 0.0
            if name == "v1_frozen":
                trades, cash, pending, blocked = replay_session(
                    possible_v1,session.quotes,ledger["cash"])
            else:
                trades, cash, pending, blocked = replay_v2(
                    possible_v2, session, ledger["cash"],
                    adaptive=name=="v2_router_adaptive_exit")
            ledger["cash"],ledger["pending"]=cash,pending
            ledger["equity"].append(cash+pending)
            ledger["trades"].extend([{"day":str(day),**x} for x in trades])
            ledger["decisions"].extend([{"day":str(day),**x} for x in decisions[name]+blocked])
        processed.append({"date":str(day),"v1_signals":stats_v1["asof_scanned_signals"],
                          "v2_signals":stats_v2["asof_scanned_signals"]})
    scores={name:metrics(l["trades"],l["cash"],l["pending"],l["equity"])
            for name,l in ledgers.items()}
    unscored=any(r["unscorable_positions"] for r in scores.values())
    status = ("BLOCKED_NO_NEW_EVENT_SESSIONS" if not fresh else
              "BLOCKED_INVALID_ASOF_FEATURES" if not processed else
              "INCOMPLETE_INVALID_SESSIONS" if skipped else
              "INCOMPLETE_UNSCOREABLE_QUOTES" if unscored else
              "EXPLORATORY_FORWARD_PAIR_COMPARISON_COMPLETED")
    report={
        "comparison":"SPY_0DTE_frozen_V1_vs_predeclared_V2_prospective_only",
        "status":status,
        "v1_frozen_commit":FROZEN_COMMIT,
        "v1_frozen_policy":FROZEN_POLICY,
        "v2_policy":V2_POLICY,"v2_version":V2_VERSION,
        "predeclared_at":"2026-10-12",
        "last_inspected_legacy_date":str(cutoff),
        "excluded_reused_sessions":[str(d) for d in sessions if d<=cutoff],
        "new_event_sessions":[str(d) for d in fresh],
        "processed":processed,"skipped":skipped,
        "input_audit":audit(event_dir),
        "results":scores,"start_settled_cash_usd":START_CASH,
        "cost_assumptions":{"fee_per_side_usd":FEE_PER_SIDE,"slip_per_share_usd":SLIP_PER_SHARE,
                            "extra_fee_stress_per_trade_usd":2},
        "no_broker_orders":True,"live_trading_unchanged":True,
        "limitations":[
            "All outcomes hypothetical event-bid paper marks, not executable broker fills.",
            "Only completed SPY bars and event-observed quotes as-of a decision are eligible.",
            "V1 and V2 use identical unseen eligible dates and the same inputs.",
            "V2 heuristic thresholds are predeclared, NOT statistically calibrated.",
            "Unscorable exit observations reserve spent premium and invalidate complete P&L inference.",
            "No event files means BLOCKED, never a fake win rate.",
        ],
    }
    output_dir.mkdir(parents=True,exist_ok=True)
    (output_dir/"comparison_manifest.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    for name,ledger in ledgers.items():
        _csv(output_dir/f"{name}_decisions.csv",ledger["decisions"],("day","reason","signal_at"))
        _csv(output_dir/f"{name}_paper_positions.csv",ledger["trades"],("day","entry_at","pnl_usd"))
    print("REGIME_V2_FORWARD_COMPARE "+json.dumps(report,sort_keys=True),flush=True)
    return report


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--event-dir",type=Path,default=Path("/data/research/forward_events"))
    p.add_argument("--legacy-dir",type=Path,default=Path("/data/research"))
    p.add_argument("--output-dir",type=Path,default=Path("/data/research/regime_v2_comparison"))
    x=p.parse_args()
    run_compare(x.event_dir,x.legacy_dir,x.output_dir)


if __name__=="__main__":
    main()
