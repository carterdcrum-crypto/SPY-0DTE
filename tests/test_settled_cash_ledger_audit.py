"""Illustrative T+1 cash feasibility: do not recycle same-day sale proceeds."""
from engine.settled_cash_ledger_audit import process_one, audit, HYPOTHETICAL_STARTING_SETTLED_CASH_USD


def row(day,time,debit,pnl):
    return {"day":day,"entry_observation_at":day+"T"+time,
            "entry_debit_usd":str(debit),"pnl_usd":str(pnl)}


def test_T1_settlement_blocks_recycling_sale_proceeds_same_day():
    days=["2026-10-05","2026-10-06","2026-10-07"]
    ledger=[
        row("2026-10-05","13:40:00",100.,50.),
        row("2026-10-05","14:10:00",100.,20.),
        row("2026-10-06","13:40:00",100.,10.),
    ]
    a=process_one(ledger,days,115.)
    assert a["actual_modeled_entries_affordable"]==2
    assert a["entries_skipped_insufficient_same_day_settled_cash"]==1
    assert a["realized_equity_end_usd"]==175.
    assert a["days_with_at_least_two_affordable_contract_entries"]==0


def test_cheap_two_contracts_possible_if_both_paid_from_settled_cash():
    days=["2026-10-05","2026-10-06"]
    ledger=[row(days[0],"13:40:00",40.,-40.),
            row(days[0],"14:10:00",40.,50.)]
    a=process_one(ledger,days,115.)
    assert a["actual_modeled_entries_affordable"]==2
    assert a["days_with_at_least_two_affordable_contract_entries"]==1
    assert a["realized_equity_end_usd"]==125.


def test_can_not_overdraw_account_after_total_loss():
    days=["2026-10-05","2026-10-06"]
    ledger=[row(days[0],"13:40:00",115.,-115.),
            row(days[1],"13:40:00",100.,10.)]
    a=process_one(ledger,days,115.)
    assert a["actual_modeled_entries_affordable"]==1
    assert a["realized_equity_end_usd"]==0.
    assert a["weeks_returning_100pct_or_more"]==0


def test_new_1000_dollar_research_balance_allows_multiple_fully_funded_debits():
    assert HYPOTHETICAL_STARTING_SETTLED_CASH_USD == 1000.0
    days=["2026-10-05","2026-10-06"]
    cases=[row(days[0],"13:40:00",400.,40.),
           row(days[0],"14:10:00",400.,-40.),
           row(days[0],"14:30:00",400.,20.)]
    summary=process_one(cases,days,HYPOTHETICAL_STARTING_SETTLED_CASH_USD)
    assert summary["actual_modeled_entries_affordable"]==2
    assert summary["entries_skipped_insufficient_same_day_settled_cash"]==1
    assert summary["realized_equity_end_usd"]==1000.
    assert summary["days_with_at_least_two_affordable_contract_entries"]==1


def test_latest_net_edge_csv_fields_and_no_trade_market_calendar(tmp_path):
    ledger=tmp_path/"trades.csv"
    ledger.write_text(
        "day,arm,entry_at,entry_debit_usd,pnl_usd\n"
        "2026-10-05,option_friction_only,2026-10-05T13:40:00+00:00,125.65,12.45\n"
    )
    # Prior source includes archived CSV filenames containing a market
    # session date, even when no entry qualified that day.
    archive=tmp_path/"archive"
    archive.mkdir()
    (archive/"spy_0dte_2026-10-05.csv.gz").write_bytes(b"")
    (archive/"spy_0dte_2026-10-06.csv.gz").write_bytes(b"")
    result=audit(ledger,tmp_path/"report.json",1000.,calendar_dir=archive)
    assert result["market_calendar_sessions_used"]==2
    assert result["missing_no_trade_sessions_from_csv"] is False
    result_arm=result["results"]["option_friction_only"]
    assert result_arm["starting_settled_cash_usd"]==1000.
    assert result_arm["actual_modeled_entries_affordable"]==1
    assert result_arm["realized_equity_end_usd"]==1012.45
