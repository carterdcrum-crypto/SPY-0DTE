"""V5 three-signal ablation and one-at-a-time entry variations. RESEARCH ONLY.

Full cash + EOD drawdown throttle, IV/RV <=1.5, delta [0.35,0.65],
max sampled option spread 5%; exact same quote-selection and exit mechanisms.
Runs 13 independently compounded simulated portfolios on archived 128 sessions.
Baseline matches prior $1,808,494.60 close-only replay precisely, every day.

Only changes ONE signal's causal-as-of entry filters for each variation,
or studies one signal on its own or the other two without it.
No inference of missing OHLCV/depth, no real orders, no live/paper modifications.
These repeatedly researched sessions are IN-SAMPLE and NONEXECUTABLE.
"""
from __future__ import annotations

import bisect
import json
import math
import os
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from .burst_research import _research_files
from .data import load_canonical_path
from .regime_hold_sensitivity import START_CASH, MAX_FIXED_MIN, liquidation_cutoff
from .v4_dynamic_size_backtest import summary
from .v5_archive_proxy import (
    FEE, SLIP, ARMS, close_proxy_signals, proxy_exit
)
from .regime_reclaim_frozen_signal import ema
from quantconnect.v5_core import Signal
from .v5_iv_delta_sensitivity import comparable
from .v5_spread_sensitivity import param_candidates as frozen_spread_candidates
from .v5_conditional_capital_study import (
    choose_qty, dd_multiplier, half_metrics, market_evidence,
    peak_to_trough_eod
)
from .weekly_options_data import NY
from quantconnect.v5_core import quality_score

FULL = ARMS[-1]
# Predeclared comparisons, not a broad optimized parameter search.
IV_CAP = 1.5
DELTA_LOW, DELTA_HIGH = .35, .65
SPREAD_CAP = .05
ARM_CONFIGS = {
    "baseline_all_three": ("all", "original"),
    "momentum_looser": ("momentum_ignition", "looser"),
    "momentum_stricter": ("momentum_ignition", "stricter"),
    "reversal_looser": ("liquidity_reversal", "looser"),
    "reversal_stricter": ("liquidity_reversal", "stricter"),
    "expansion_looser": ("volatility_expansion", "looser"),
    "expansion_stricter": ("volatility_expansion", "stricter"),
    "momentum_only": ("momentum_ignition", "only"),
    "reversal_only": ("liquidity_reversal", "only"),
    "expansion_only": ("volatility_expansion", "only"),
    "without_momentum": ("momentum_ignition", "without"),
    "without_reversal": ("liquidity_reversal", "without"),
    "without_expansion": ("volatility_expansion", "without"),
}

VARIATIONS = {
    "momentum_looser": "8-close breakout, min efficiency 0.18, min 3-minute momentum 2bps (vs 12, 0.24, 3bps)",
    "momentum_stricter": "16-close breakout, min efficiency 0.34, min 3-minute momentum 4bps",
    "reversal_looser": "prior 15-close range-break and recross, max efficiency 0.75 (vs prior 20, 0.60)",
    "reversal_stricter": "prior 25-close range-break and recross, max efficiency 0.45",
    "expansion_looser": "6-close compression and breakout, compression ratio <=0.90, min momentum 1.5bps (vs 8, 0.75, 2)",
    "expansion_stricter": "10-close compression and breakout, compression ratio <=0.55, min momentum 3bps",
}



def varied_signals(frames, i: int, variant: str):
    """Signals use only minute closes and RVOL as-of bar i, never future bars.

    Unmodified signal families use the frozen base close_proxy_signals exactly.
    Returns original ordering: momentum, reversal, expansion.
    """
    baseline = tuple(close_proxy_signals(frames, i, challenger=True))
    if variant == "baseline_all_three":
        return baseline
    target, kind = ARM_CONFIGS[variant]
    def family(sig):
        return sig.setup.replace("_CLOSE_PROXY","")
    if kind == "only":
        return tuple(sig for sig in baseline if family(sig) == target)
    if kind == "without":
        return tuple(sig for sig in baseline if family(sig) != target)
    assert kind in ("looser", "stricter")

    # Copy the frozen signal's data-integrity and timestamp gates. Failure
    # must mean no signals, including the altered families.
    if i < 35:
        return ()
    recent_frames = frames[i-35:i+1]
    day = frames[i].timestamp.astimezone(NY).date()
    if any(f.timestamp.astimezone(NY).date() != day
           or not math.isfinite(f.market.spot) or f.market.spot <= 0
           for f in recent_frames):
        return ()
    if any((b.timestamp-a.timestamp).total_seconds() != 60
           for a,b in zip(recent_frames,recent_frames[1:])):
        return ()
    rvol = frames[i].market.volume_ratio
    if rvol < 1.25:
        return ()

    spots = [f.market.spot for f in frames]
    recent = spots[i-35:i+1]
    now, prev = spots[i], spots[i-1]
    fast = ema(recent, 8)
    slow = ema(recent[:-1], 21)
    older = ema(recent[:-11], 21)
    trend = (slow/older-1)*1e4 if older > 0 else 0.
    distance = sum(abs(spots[j]-spots[j-1]) for j in range(i-14,i+1))
    efficiency = abs(now-spots[i-15])/distance if distance > 0 else 0.
    momentum = (now/spots[i-3]-1)*1e4
    custom = []

    def emit(side):
        custom.append(Signal(side, target+"_CLOSE_PROXY",
                             rvol, trend, efficiency))

    if target == "momentum_ignition":
        lookback,min_eff,min_bps = ((8,.18,2.) if kind == "looser"
                                   else (16,.34,4.))
        high, low = max(spots[i-lookback:i]),min(spots[i-lookback:i])
        if (efficiency >= min_eff and now > high and fast > slow
                and trend >= 2 and momentum >= min_bps):
            emit("call")
        elif (efficiency >= min_eff and now < low and fast < slow
                and trend <= -2 and momentum <= -min_bps):
            emit("put")
    elif target == "liquidity_reversal":
        lookback,max_eff = ((15,.75) if kind == "looser"
                            else (25,.45))
        old = spots[i-lookback-2:i-2]
        if prev > max(old) and now < max(old) and now < prev and efficiency < max_eff:
            emit("put")
        elif prev < min(old) and now > min(old) and now > prev and efficiency < max_eff:
            emit("call")
    elif target == "volatility_expansion":
        compact_n,compact_ratio,min_bps=((6,.90,1.5) if kind == "looser"
                                         else (10,.55,3.))
        wide = max(spots[i-32:i-8])-min(spots[i-32:i-8])
        narrow = max(spots[i-compact_n:i])-min(spots[i-compact_n:i])
        if wide > 0 and narrow/wide <= compact_ratio:
            if (now > max(spots[i-compact_n:i]) and now > prev
                    and momentum >= min_bps and fast > slow):
                emit("call")
            elif (now < min(spots[i-compact_n:i]) and now < prev
                    and momentum <= -min_bps and fast < slow):
                emit("put")
    else:
        raise ValueError("Unrecognized signal family")
    combined=[]
    for family_name in ("momentum_ignition","liquidity_reversal",
                        "volatility_expansion"):
        if family_name==target:
            combined.extend(custom)
        else:
            combined.extend(sig for sig in baseline if family(sig)==family_name)
    return tuple(combined)


def param_candidates(frames, *, variant: str):
    """Frozen sampled-quote selection with only signal-conditions different."""
    iv_cap,delta_low,delta_high=IV_CAP,DELTA_LOW,DELTA_HIGH
    spread_cap=SPREAD_CAP
    times = [f.timestamp + timedelta(minutes=1) for f in frames]
    entries = []
    for i in range(35, len(frames)):
        signals = varied_signals(frames, i, variant)
        if not signals:
            continue
        event_time = times[i]
        for sig in signals:
            start = event_time + timedelta(minutes=1)
            end = event_time + timedelta(minutes=2)
            idx = bisect.bisect_left(times, start)
            chosen = None
            for j in range(idx, len(frames)):
                if times[j] > end:
                    break
                qlist = [q for q in frames[j].options
                         if q.right == sig.side and q.bid > 0 and q.ask > q.bid
                         and delta_low <= abs(q.delta) <= delta_high
                         and q.spread_fraction <= .15]
                if qlist:
                    q = min(qlist, key=lambda q: (
                        abs(abs(q.delta)-.50),
                        q.spread_fraction,
                        abs(q.strike-frames[j].market.spot),
                        q.symbol))
                    chosen = (j, q)
                    break
            if chosen is None:
                continue
            j, q = chosen
            market = frames[j].market
            if (q.spread_fraction > spread_cap or market.realized_volatility <= 0 or
                    q.implied_volatility <= 0 or
                    q.implied_volatility / market.realized_volatility > iv_cap):
                continue
            score = quality_score(sig, q.spread_fraction, q.delta,
                                  q.implied_volatility, market.realized_volatility)
            entries.append({
                "signal": {
                    "at": event_time, "side": sig.side, "setup": sig.setup,
                    "volume_ratio": sig.rvol, "trend_bps": sig.trend_bps,
                    "efficiency": sig.efficiency,
                },
                "entry_idx": j, "entry_at": times[j], "quote": q,
                "score": score,
                "debit": round((q.ask+SLIP)*100+FEE, 2),
                "realized_vol_at_entry": market.realized_volatility,
            })
    return sorted(entries, key=lambda x: (x["entry_at"], x["quote"].symbol))


def comparable(item):
    return (item["signal"]["at"], item["signal"]["side"],
            item["signal"]["setup"], item["entry_idx"], item["entry_at"],
            item["quote"].symbol, item["score"], item["debit"])


def replay(paths, *, starting_cash=START_CASH):
    if not paths:
        raise RuntimeError("No archived Railway option CBBO sessions; no fabricated backtest")
    states = {key: {
        "settled": float(starting_cash), "pending": 0.,
        "daily": [], "trades": [], "skips": Counter(), "exits": Counter()
    } for key in ARM_CONFIGS}
    dates = []
    total_eligible = Counter()
    for file in paths:
        frames = list(load_canonical_path(file))
        if not frames:
            continue
        day = frames[0].timestamp.astimezone(NY).date()
        dates.append(str(day))
        cutoff = liquidation_cutoff(day)

        # Exact daily equality to the previously executed 5% spread study.
        frozen = frozen_spread_candidates(frames, spread_cap=SPREAD_CAP)
        candidates = {
            key: param_candidates(frames, variant=key)
            for key in ARM_CONFIGS
        }
        assert [comparable(x) for x in candidates["baseline_all_three"]] == [
            comparable(x) for x in frozen], f"Current V5 5% spread baseline drift: {day}"

        for key, entries in candidates.items():
            total_eligible[key] += len(entries)
            state = states[key]
            state["settled"] = round(state["settled"] + state["pending"], 8)
            state["pending"] = 0.
            opening = state["settled"]
            prior_peak = max([starting_cash] + [x["equity_usd"] for x in state["daily"]])
            busy_until = None
            observations = defaultdict(set)
            for item in entries:
                observations[(item["entry_at"], item["signal"]["side"])].add(
                    item["signal"]["setup"])

            for item in entries:
                at = item["entry_at"]
                if at >= cutoff-timedelta(minutes=MAX_FIXED_MIN+1):
                    state["skips"]["LATE_ENTRY"] += 1
                    continue
                if busy_until is not None and at <= busy_until:
                    state["skips"]["BUSY"] += 1
                    continue
                evidence = market_evidence(
                    item, observations[(at, item["signal"]["side"])])
                qty, fraction = choose_qty(
                    ("full_throttle",), item=item,
                    opening=opening, settled=state["settled"],
                    peak=prior_peak, evidence=evidence,
                    starting_cash=starting_cash,
                )
                if qty < 1:
                    state["skips"]["CASH_OR_RISK_BUDGET"] += 1
                    continue
                debit = item["debit"]
                if qty * debit > state["settled"] + 1e-8:
                    raise AssertionError("T+1 cash settlement violated")
                exit_info = proxy_exit(FULL, item, frames, cutoff)
                state["settled"] = round(state["settled"] - qty*debit, 8)
                trade = {
                    "day": str(day), "setup": item["signal"]["setup"],
                    "contracts": qty, "entry_at": at.isoformat(),
                    "score": item["score"], "risk_tier": evidence["tier"],
                    "risk_fraction": round(fraction, 4),
                    "confluence": evidence["confluence"],
                    "entry_debit_usd": debit,
                    "pnl_usd": None if exit_info is None
                        else round(qty*exit_info["pnl_usd"], 6),
                }
                if exit_info is None:
                    busy_until = cutoff
                    trade["exit_reason"] = "NO_VERIFIED_TERMINAL_BID_PREMIUM_RESERVED"
                else:
                    state["pending"] += max(0., qty*debit + trade["pnl_usd"])
                    busy_until = exit_info["exit_at"]
                    trade["exit_reason"] = exit_info["exit_reason"]
                    state["exits"][exit_info["exit_reason"]] += 1
                state["trades"].append(trade)
            state["daily"].append({"day": str(day),
                                   "equity_usd": round(state["settled"]+state["pending"],4)})
        if len(dates) % 20 == 0:
            print("V5_SIGNAL_PROGRESS "+json.dumps({"sessions":len(dates),"day":str(day)}), flush=True)

    if not dates:
        raise RuntimeError("No usable historical sessions")
    results = {}
    for key, s in states.items():
        middle = len(s["daily"]) // 2
        result = {
            **summary(s, starting_cash=starting_cash),
            **peak_to_trough_eod(s["daily"], starting_cash),
            "eligible_setup_quote_candidates": total_eligible[key],
            "first_half": half_metrics(s["daily"], 0, middle, starting_cash),
            "second_half": half_metrics(
                s["daily"], middle, len(s["daily"]),
                s["daily"][middle-1]["equity_usd"]),
            "max_hypothetical_contracts_in_one_trade":
                max([x["contracts"] for x in s["trades"]], default=0),
            "total_trades_recorded": len(s["trades"]),
        }
        results[key] = result
    # Fail rather than publish if 5% spread historical baseline has drifted.
    baseline = results["baseline_all_three"]
    assert abs(float(baseline["ending_equity_usd"])-1808494.60) < .02, (
        "Historical $1,808,494.60 at delta .35–.65/IV1.5/spread5% "
        "did not reproduce: " + repr(baseline))
    assert len(dates) == 128, f"Expected 128 archived sessions, found {len(dates)}"
    return {
        "status": "IN_SAMPLE_CLOSE_ONLY_THREE_SIGNAL_SWEEP_NO_EXECUTABLE_DEPTH",
        "first_day": min(dates), "last_day": max(dates),
        "sessions": len(dates), "starting_cash": starting_cash,
        "arms": ARM_CONFIGS, "results": results,
        "variant_descriptions": VARIATIONS,
        "fixed_iv_rv_cap": IV_CAP,
        "fixed_abs_delta_range": [DELTA_LOW,DELTA_HIGH],
        "fixed_spread_fraction": SPREAD_CAP,
        "baseline_match": True,
        "changed_only": ["entry signal conditions separately for three setups",
                         "optional isolate or remove one setup"],
        "preserved": ["three V5 close-proxy signal types","full-cash EOD drawdown throttle",
                      "35% hard stop","25% trailing stop","18-minute maximum hold",
                      "IV/RV <= 1.5 and absolute delta 0.35–0.65",
                      "5% option spread","sampled option bid/ask",
                      "1 cent/share slippage and 65 cents/contract per side",
                      "next-session cash settlement","one position at a time"],
        "limitations": [
            "Previously repeatedly studied 128 sessions; no independent holdout",
            "Minute-sampled option CBBO is not executable NBBO and size is absent",
            "SPY close and volume_ratio proxies, not genuine full 1-minute OHLCV",
            "No market impact or live limit order fill validation",
            "Hypothetical contracts may vastly exceed actual displayed depth",
            "Max end-of-day drawdown understates unknown intraday drawdown",
            "Historical modeled performance is not a live profit forecast",
        ],
        "live_and_paper_unchanged": True,
        "data_purchased": False,
        "broker_orders": 0,
    }


def main():
    root = Path(os.getenv("RESEARCH_DATA_DIR", "/data/research"))
    paths = _research_files(root)
    report = replay(paths)
    output = root / "v5_signal_sweep_2026_10_28" / "report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    # One short log line per arm so Railway bounded logs can be read easily.
    print("V5_SIGNAL_META "+json.dumps({k:report[k] for k in
        ("status","first_day","last_day","sessions","baseline_match")}),flush=True)
    for key, result in report["results"].items():
        print("V5_SIGNAL_RESULT "+json.dumps({
            "arm":key,"variation":ARM_CONFIGS[key],**result},sort_keys=True),flush=True)
    print("V5_SIGNAL_REPORT_SAVED "+str(output), flush=True)
    # Restore the pre-existing research watch process after completing the
    # sweep. Does NOT touch live/paper workers or call broker.
    os.execvp("python", ["python","-m","engine.forward_watch"])


if __name__ == "__main__":
    main()
