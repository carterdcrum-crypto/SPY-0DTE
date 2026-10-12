"""V3 exit policy has no 10-minute timeout, but a hard expiry buffer."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path
import pytest

from engine.regime_reclaim_v3 import replay_open_ended_v3, run_v3_comparison

D=date(2026,10,12)
T=datetime(2026,10,12,14,0,tzinfo=timezone.utc)


def _session(with_cutoff=True):
    t=T
    symbol="SPY261012C00600000"
    q=SimpleNamespace(observed_at=t,symbol=symbol,ask=1.0,
                      bid=.99,right="call")
    # Last quote is >10 min after entry, and above break-even.
    quotes=[SimpleNamespace(observed_at=t+timedelta(minutes=k),
             symbol=symbol,bid=(1.05 if k<12 else 1.15),
             ask=(1.06 if k<12 else 1.16))
             for k in (1,3,10,11,12)]
    if with_cutoff:
        quotes.append(SimpleNamespace(observed_at=datetime(2026,10,12,19,45,tzinfo=timezone.utc),
                   symbol=symbol,bid=1.18,ask=1.19))
    return SimpleNamespace(day=D,bars=[],quotes=quotes),q


def test_v3_does_not_exit_automatically_at_ten_minutes():
    session,q=_session()
    candidate={"option":q,"signal":{"at":T-timedelta(minutes=1),
        "setup":"trend_reclaim"},"debit":101.65}
    rows,cash,pending,decisions=replay_open_ended_v3([candidate],session,1000.)
    assert len(rows)==1
    assert rows[0]["exit_at"]==datetime(2026,10,12,19,45,tzinfo=timezone.utc).isoformat()
    assert rows[0]["exit_reason"]=="mandatory_pre_expiry_liquidation"
    assert cash==898.35
    assert pending>0
    assert decisions==[]


def test_unscorable_last_quote_reserves_premium_without_fake_profit():
    session,q=_session(with_cutoff=False)
    candidate={"option":q,"signal":{"at":T-timedelta(minutes=1),
        "setup":"trend_reclaim"},"debit":101.65}
    rows,cash,pending,_=replay_open_ended_v3([candidate],session,1000.)
    assert rows[0]["pnl_usd"] is None
    assert rows[0]["reason"]=="UNSCOREABLE_MISSING_LIQUIDATION_QUOTE"
    assert cash==898.35
    assert pending==0


def test_expiry_cutoff_blocks_last_minute_new_entry():
    session,q=_session()
    q.observed_at=datetime(2026,10,12,19,44,30,tzinfo=timezone.utc)
    item={"option":q,"signal":{"at":T,"setup":"trend_reclaim"},"debit":101.65}
    results,cash,pending,decisions=replay_open_ended_v3([item],session,1000.)
    assert results==[]
    assert decisions[0]["reason"]=="EXPIRY_BUFFER_CUTOFF"
    assert cash==1000


def test_v3_comparator_without_new_event_files_is_honestly_blocked(tmp_path):
    legacy=tmp_path/"legacy"
    legacy.mkdir()
    (legacy/"spy_0dte_2026-10-08.csv.gz").write_bytes(b"inspected")
    events=tmp_path/"events"
    events.mkdir()
    out=tmp_path/"out"
    report=run_v3_comparison(events,legacy,out)
    assert report["status"]=="BLOCKED_NO_NEW_EVENT_SESSIONS"
    assert report["v3_has_no_fixed_max_minutes"]
    assert set(report["results"])=={
        "v1_frozen","v2_router_fixed_exit","v2_router_adaptive_exit","v3_no_max_hold"}
    assert report["results"]["v3_no_max_hold"]["win_rate_pct"] is None
    assert report["results"]["v3_no_max_hold"]["hypothetical_equity_usd"]==1000
    assert (out/"v3_no_max_hold_comparison.json").is_file()
    assert report["no_real_orders"]
