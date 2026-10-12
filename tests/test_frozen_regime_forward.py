"""Fail-closed prospective 0DTE data gate: no stale data or invented fills."""
from datetime import datetime,date,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
import csv
import pytest

from engine.frozen_regime_forward import (
    run,session_plan,replay_session,latest_legacy_day,
    FROZEN_COMMIT,FROZEN_POLICY,START_CASH,
)


def make_archive(tmp_path,day="2026-10-08"):
    d=tmp_path/"archive";d.mkdir()
    (d/f"spy_0dte_{day}.csv.gz").write_bytes(b"historical only")
    return d


def test_no_future_event_data_blocks_without_fake_win_rate(tmp_path):
    archive=make_archive(tmp_path)
    real=tmp_path/"event";real.mkdir()
    result=run(real,archive,tmp_path/"manifest")
    assert result["status"]=="BLOCKED_NO_NEW_EVENT_SESSIONS"
    assert result["scorable_positions"]==0
    assert result["hypothetical_equity_usd"]==START_CASH
    assert result["frozen_git_sha"]==FROZEN_COMMIT
    assert result["frozen_arm"]==FROZEN_POLICY
    assert result["broker_orders"]==0
    assert (tmp_path/"manifest"/"manifest.json").is_file()
    assert latest_legacy_day(archive)==date(2026,10,8)


def test_same_or_older_event_sessions_are_explicitly_excluded(tmp_path):
    archive=make_archive(tmp_path,"2026-10-08")
    event=tmp_path/"event";event.mkdir()
    (event/"spy_bars_2026-10-08.csv").write_text("not parseable")
    (event/"spy_option_quotes_2026-10-08.csv").write_text("not parseable")
    result=run(event,archive,tmp_path/"out")
    assert result["status"]=="BLOCKED_NO_NEW_EVENT_SESSIONS"
    assert result["excluded_reused_or_pre_cutoff_event_sessions"]==["2026-10-08"]


def test_required_option_iv_and_spy_rvol_not_silently_invented(tmp_path):
    event=tmp_path/"event";event.mkdir()
    # Monday 2026-10-12: strict exchange opening session.
    with (event/"spy_bars_2026-10-12.csv").open("w") as f:
        f.write("ts_start,open,high,low,close,volume\n")
        f.write("2026-10-12T13:30:00+00:00,600,601,599,600,20000\n")
    with (event/"spy_option_quotes_2026-10-12.csv").open("w") as f:
        f.write("observed_at,symbol,expiration,right,strike,bid,ask,delta,bid_size,ask_size,volume,open_interest,schema\n")
        f.write("2026-10-12T13:32:00+00:00,SPY261012C00600000,2026-10-12,call,600,1,1.05,0.5,20,20,500,600,cmbp-1\n")
    with pytest.raises(ValueError,match="missing as-of fields"):
        session_plan(event,date(2026,10,12))


def test_event_shadow_paper_respects_one_position_and_t1_cash(monkeypatch):
    import engine.frozen_regime_forward as mod
    t=datetime(2026,10,12,13,50,tzinfo=timezone.utc)
    q=SimpleNamespace(observed_at=t,bid=.9,ask=1.0,symbol="SPY_TEST",
                      right="call")
    c={"signal":{"at":t-timedelta(minutes=1),"setup":"trend_reclaim"},
       "option":q,"debit":101.65}
    # Returned quotes do not count as settled intra-day buying power.
    future=[SimpleNamespace(observed_at=t+timedelta(minutes=m),
        symbol="SPY_TEST",bid=1.1,ask=1.12)
        for m in range(1,11)]
    rows,cash,pending,decisions=replay_session([c,c],future,101.65)
    assert len(rows)==1
    assert cash==0
    assert pending>0
    assert len(decisions)==1
    assert decisions[0]["reason"]=="ONE_POSITION_BUSY"
    assert rows[0]["exit_at"] is not None


def test_missing_exit_quote_cannot_be_marked_as_gain():
    t=datetime(2026,10,12,13,50,tzinfo=timezone.utc)
    q=SimpleNamespace(observed_at=t,bid=.9,ask=1.0,
                      symbol="SPY_TEST",right="call")
    c={"signal":{"at":t-timedelta(minutes=1),"setup":"trend_reclaim"},
       "option":q,"debit":101.65}
    rows,cash,pending,decisions=replay_session([c],[],1000.)
    assert rows[0]["pnl_usd"] is None
    assert cash==898.35
    assert pending==0.
