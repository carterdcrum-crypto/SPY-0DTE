"""Research-only conditional capital-allocation sweep for restored V5.

Uses recorded SPY 1-minute CLOSE proxy + archived option minute CBBO, not true
OHLCV V5 nor verifiable event-time NBBO. Never assumes hidden depth is executable.
Compare as-of risk budgets across identical V5 three-signal entries, 18-minute
hold, 35% premium stop, 25% premium trail and original 1.5x IV/RV gate.
No broker interaction, no purchased data, no production configuration changes.
"""
from __future__ import annotations

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
from .v5_archive_proxy import ARMS, proxy_candidates, proxy_exit, proxy_size
from .weekly_options_data import NY

FULL = ARMS[-1]
ADAPTIVE = ARMS[2]
MOMENTUM = "momentum_ignition_CLOSE_PROXY"
REVERSAL = "liquidity_reversal_CLOSE_PROXY"
EXPANSION = "volatility_expansion_CLOSE_PROXY"

# All changes concern quantity ONLY. Every policy has identical three-signal
# entry order and original exits; no retrospective choice of future signals.
POLICIES = {
    "unrestricted_all_settled_cash": ("full",),
    "restored_v5_adaptive": ("adaptive",),
    "flat_20pct_equity": ("flat", .20),
    "flat_35pct_equity": ("flat", .35),
    "flat_50pct_equity": ("flat", .50),
    "conditional_conservative": ("conditional", (.08, .16, .30), True),
    "conditional_balanced": ("conditional", (.15, .30, .55), True),
    "conditional_aggressive": ("conditional", (.25, .50, .85), True),
    "conditional_balanced_no_dd_throttle": ("conditional", (.15, .30, .55), False),
    "conditional_full_only_high_quality": ("conditional", (.18, .40, 1.0), True),
    "full_cash_with_dd_throttle": ("full_throttle",),
}


def market_evidence(item, observed_setups):
    """Fixed predeclared gates, all available at the entry timestamp.

    These are heuristics, NOT an inferred win probability. Coincident signals
    count only if observable at this SAME entry time and on the SAME side.
    """
    sig = item["signal"]
    q = item["quote"]
    score = int(item["score"])
    trend = abs(float(sig["trend_bps"]))
    efficiency = float(sig["efficiency"])
    setup = sig["setup"]
    strong_trend = trend >= 4 and efficiency >= .40
    reversal_regime = trend <= 6 and efficiency <= .40
    expansion_regime = trend >= 2 and efficiency >= .25
    aligned = ((setup == MOMENTUM and strong_trend) or
               (setup == REVERSAL and reversal_regime) or
               (setup == EXPANSION and expansion_regime))
    iv_ratio = q.implied_volatility / item.get("realized_vol_at_entry", 1)
    # Archive has actual realized vol in the observed entry minute frame.
    # If missing/invalid, do not award the IV-quality bonus.
    iv_bonus = (math.isfinite(iv_ratio) and 0 < iv_ratio <= 1.25)
    other_setups = observed_setups - {setup}
    confluence = bool(other_setups)
    points = (int(score >= 4)
              + int(float(q.spread_fraction) <= .015)
              + int(float(sig["volume_ratio"]) >= 1.75)
              + int(iv_bonus)
              + int(aligned)
              + int(confluence))
    tier = 2 if points >= 4 else 1 if points >= 2 else 0
    return {"points": points, "tier": tier, "aligned": aligned,
            "confluence": confluence, "iv_ratio": iv_ratio}


def dd_multiplier(prior_opening, prior_peak):
    """Only PREVIOUSLY CLOSED session equity and this session opening cash."""
    dd = max(0., 1 - prior_opening/prior_peak) if prior_peak > 0 else 1.
    if dd < .02:
        return 1.
    if dd < .05:
        return .70
    if dd < .10:
        return .40
    return .15


def choose_qty(policy, *, item, opening, settled, peak, evidence,
               starting_cash=START_CASH):
    """Entire simulated long-option debit remains at risk; never borrow."""
    if not all(math.isfinite(x) for x in (opening, settled, peak, item["debit"])):
        return 0, 0.
    debit = float(item["debit"])
    if min(opening, settled, peak, debit) <= 0:
        return 0, 0.
    cash_qty = int((settled + 1e-8)//debit)
    mode = policy[0]
    if mode == "full":
        return cash_qty, min(1., cash_qty*debit/opening)
    if mode == "adaptive":
        qty = proxy_size(ADAPTIVE, equity=opening, settled=settled,
                         peak=peak, debit=debit, score=item["score"],
                         starting_cash=starting_cash)
        return min(qty, cash_qty), min(1., qty*debit/opening)
    if mode == "flat":
        fraction = policy[1]
    elif mode == "full_throttle":
        fraction = dd_multiplier(opening, peak)
    elif mode == "conditional":
        fraction = policy[1][evidence["tier"]]
        if policy[2]:
            fraction *= dd_multiplier(opening, peak)
    else:
        raise ValueError("Unknown risk policy")
    qty = min(cash_qty, int((opening*fraction + 1e-8)//debit))
    return max(0, qty), fraction


def peak_to_trough_eod(daily, starting_cash):
    """Correct maximum *percentage* drawdown, including initial capital."""
    peak = starting_cash
    dd = 0.
    peak_date = "initial_capital"
    trough = peak_date
    worst_peak = peak_date
    for row in daily:
        equity = row["equity_usd"]
        if equity > peak:
            peak = equity
            peak_date = row["day"]
        current = (peak-equity)/peak if peak else 1.
        if current > dd:
            dd = current
            worst_peak = peak_date
            trough = row["day"]
    return {"true_max_eod_drawdown_pct": round(dd*100, 2),
            "worst_dd_peak_day":worst_peak, "worst_dd_trough_day":trough}


def half_metrics(daily, start, end, initial):
    section = daily[start:end]
    if not section:
        return None
    return {
        "ending_equity_usd": section[-1]["equity_usd"],
        "return_pct": round(100*(section[-1]["equity_usd"]/initial-1), 2) if initial else None,
        "max_eod_dd_pct": peak_to_trough_eod(section, initial)["true_max_eod_drawdown_pct"],
    }


def replay(paths, *, starting_cash=START_CASH):
    if not paths:
        raise ValueError("Missing genuine Railway archived sessions")
    states = {name: {
        "settled": starting_cash, "pending": 0., "daily": [],
        "trades": [], "skips": Counter(), "exits": Counter(),
        "tier_counts": Counter(),
    } for name in POLICIES}
    dates = []
    for path in paths:
        frames = list(load_canonical_path(path))
        if not frames:
            continue
        day = frames[0].timestamp.astimezone(NY).date()
        dates.append(str(day))
        cutoff = liquidation_cutoff(day)
        # All 11 arms share exactly the original signals, candidate quotes,
        # score, entry timestamps and stops, irrespective of prior outcomes.
        entries = proxy_candidates(frames, challenger=True)
        # A competing signal is observable only at the same entry timestamp
        # and same put/call side: no later lookahead, no cross-direction counts.
        observations = defaultdict(set)
        for item in entries:
            observations[(item["entry_at"], item["signal"]["side"])].add(
                item["signal"]["setup"])
        for item in entries:
            market = frames[item["entry_idx"]].market
            item["realized_vol_at_entry"] = market.realized_volatility
        for label, policy in POLICIES.items():
            s = states[label]
            s["settled"] = round(s["settled"]+s["pending"], 8)
            s["pending"] = 0.
            opening = s["settled"]
            previous_peak = max([starting_cash]+[x["equity_usd"] for x in s["daily"]])
            busy_until = None
            for item in entries:
                at = item["entry_at"]
                if at >= cutoff-timedelta(minutes=MAX_FIXED_MIN+1):
                    s["skips"]["LATE_ENTRY"] += 1
                    continue
                if busy_until is not None and at <= busy_until:
                    s["skips"]["BUSY"] += 1
                    continue
                evidence = market_evidence(
                    item, observations[(at, item["signal"]["side"])])
                qty, target_fraction = choose_qty(
                    policy, item=item, opening=opening, settled=s["settled"],
                    peak=previous_peak, evidence=evidence,
                    starting_cash=starting_cash)
                if qty < 1:
                    s["skips"]["CASH_OR_RISK_BUDGET"] += 1
                    continue
                debit = item["debit"]
                if qty*debit > s["settled"]+1e-8:
                    raise AssertionError("T+1 settled cash violated")
                # Identical V5 18-min max hold, original stops, exit logic.
                exit_info = proxy_exit(FULL, item, frames, cutoff)
                s["settled"] = round(s["settled"]-qty*debit, 8)
                s["tier_counts"][str(evidence["tier"])] += 1
                row = {"day":str(day), "setup":item["signal"]["setup"],
                       "contracts":qty, "entry_at":at.isoformat(),
                       "score":item["score"], "risk_tier":evidence["tier"],
                       "risk_fraction":round(target_fraction, 4),
                       "confluence":evidence["confluence"],
                       "entry_debit_usd":debit,
                       "pnl_usd": None if exit_info is None
                       else round(qty*exit_info["pnl_usd"], 6)}
                if exit_info is None:
                    busy_until = cutoff
                    row["exit_reason"] = "NO_VERIFIED_TERMINAL_BID_PREMIUM_RESERVED"
                else:
                    s["pending"] += max(0., qty*debit+row["pnl_usd"])
                    busy_until = exit_info["exit_at"]
                    row["exit_reason"] = exit_info["exit_reason"]
                    s["exits"][exit_info["exit_reason"]] += 1
                s["trades"].append(row)
            s["daily"].append({
                "day":str(day),
                "equity_usd":round(s["settled"]+s["pending"], 4)})
        if len(dates)%20==0:
            print("V5_CONDITIONAL_PROGRESS " + json.dumps({
                "sessions":len(dates), "day":str(day)}), flush=True)
    if not dates:
        raise ValueError("Archive had no historical trading sessions")

    results = {}
    for label,s in states.items():
        by_setup = defaultdict(list)
        for trade in s["trades"]:
            if trade["pnl_usd"] is not None:
                by_setup[trade["setup"]].append(trade)
        middle = len(s["daily"])//2
        results[label] = {
            **summary(s, starting_cash=starting_cash),
            **peak_to_trough_eod(s["daily"], starting_cash),
            "tier_counts":dict(s["tier_counts"]),
            "first_half": half_metrics(s["daily"],0,middle,starting_cash),
            "second_half": half_metrics(s["daily"],middle,len(s["daily"]),
                                         s["daily"][middle-1]["equity_usd"]),
            "by_signal":{setup:{
                "trades":len(rows),
                "modeled_net_profit_usd":round(sum(t["pnl_usd"] for t in rows),2),
            } for setup, rows in sorted(by_setup.items())},
        }
    base = results["unrestricted_all_settled_cash"]
    for v in results.values():
        v["eod_dd_delta_vs_unrestricted_pp"] = round(
            v["true_max_eod_drawdown_pct"]-base["true_max_eod_drawdown_pct"], 2)
    return {
        "status":"IN_SAMPLE_CLOSE_ONLY_V5_CONDITIONAL_SIZING_NOT_TRUE_V5",
        "sessions":len(dates), "first_day":min(dates), "last_day":max(dates),
        "starting_cash_usd":starting_cash,
        "risk_policies":POLICIES,
        "only_changed_component":"quantity; all three restored V5 signal entries and exits held constant",
        "cash_account":"Only previously settled cash is usable; no same-day sale proceeds recycling",
        "current_proxy_stop_loss_pct":35, "premium_trailing_stop_pct":25,
        "maximum_hold_minutes":18, "iv_to_realized_vol_cap":1.5,
        "no_broker_orders":True,"no_data_purchases":True,
        "main_and_live_unchanged":True,
        "important_limitations":[
            "Previously repeatedly inspected 128-session archive, NOT an independent holdout",
            "Not genuine 1-minute OHLCV V5 signals; SPY highs/lows and full volume missing",
            "Executable event-time option NBBO, displayed depth and market impact absent",
            "Contract counts including unrestricted baseline cannot be treated as fillable",
            "Intraday drawdown not observable. Historical max EOD DD is not max intraday DD",
            "Two chronological halves are diagnostics, NOT independent unseen validation",
        ],
        "results":results,
    }


def main():
    root=Path(os.getenv("RESEARCH_DATA_DIR","/data/research"))
    report=replay(_research_files(root))
    path=root/"v5_conditional_capital_2026_10_25"/"report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print("V5_CONDITIONAL_RESULT "+json.dumps(report,sort_keys=True),flush=True)
    os.execvp("python",["python","-m","engine.forward_watch"])


if __name__=="__main__":
    main()
