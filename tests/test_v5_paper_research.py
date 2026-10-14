"""Research-only V5 Railway bridge never submits Webull orders."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from engine.v5_paper_research import paper_research_candidate
from engine.weekly_options_data import Bar


def _bars():
    et=ZoneInfo("America/New_York")
    start=datetime(2026,10,13,9,30,tzinfo=et)
    bars=[]
    for i in range(72):
        close=500+i*.14
        volume=60000 if i==70 else 15000
        bars.append(Bar(start+timedelta(minutes=i),close-.03,
                        close+.04,close-.06,close,volume))
    return bars


def test_reject_live_and_unrestricted_arm_with_zero_orders():
    now=_bars()[-1].available_at+timedelta(seconds=1)
    proposal,diag=paper_research_candidate(now,_bars(),(),equity=1000,
        settled=1000,peak=1000,variant="v5_unrestricted_research")
    assert proposal is None
    assert diag["state"]=="RESEARCH_ONLY_REFUSAL"


def test_stale_and_missing_real_quote_never_create_order():
    bars=_bars()
    now=bars[-1].available_at+timedelta(seconds=1)
    proposal,diag=paper_research_candidate(now,bars,(),equity=1000,
        settled=1000,peak=1000)
    assert proposal is None
    assert diag["state"] in ("NO_ASOF_0DTE_NBBO_OR_SIZE","NO_V5_SIGNAL")
    proposal,diag=paper_research_candidate(
        now+timedelta(minutes=4),bars,(),equity=1000,
        settled=1000,peak=1000)
    assert proposal is None
    assert diag["state"]=="WAITING_AUTHENTIC_M1"


def test_quote_observation_is_validated_and_research_only():
    bars=_bars()
    now=bars[-1].available_at+timedelta(seconds=1)
    prior=[b.close for b in bars[:-1]]
    from quantconnect.v5_core import realized_volatility
    rv=realized_volatility(prior)
    q=SimpleNamespace(source="webull_production",
        timestamp_quality="option_quote_timestamp",
        expiration=now.astimezone(ZoneInfo("America/New_York")).date(),
        right="call",option_symbol="synthetic-research-only",
        timestamp=now,received_at=now,underlying_timestamp=now,
        bid=.45,ask=.46,bid_size=30,ask_size=30,
        greeks=SimpleNamespace(delta=.5,implied_volatility=rv*.7))
    proposal,diag=paper_research_candidate(now,bars,[q],equity=1000,
        settled=1000,peak=1000)
    assert proposal is not None, diag
    assert proposal["research_only"] is True
    assert proposal["send_order"] is False
    assert proposal["quantity"]>0
    assert diag["broker_order_authorized"] is False
