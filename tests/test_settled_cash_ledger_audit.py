"""Illustrative T+1 cash feasibility: do not recycle same-day sale proceeds."""
from engine.settled_cash_ledger_audit import process_one


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
