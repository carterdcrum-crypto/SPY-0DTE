from datetime import date, datetime, timezone

import pytest

from engine.paper_account import PaperAccountStore


def open_position(account):
    account.open_long(symbol="SPY260925C00670000", quantity=3, fill_price=50,
                      opened_at=datetime(2026, 9, 25, 15, tzinfo=timezone.utc),
                      entry_spot=670, strategy="test", stop_price=30,
                      target_price=80, max_hold_seconds=600, reason="test")


def close_position(account):
    return account.close_long(symbol="SPY260925C00670000", quantity=3, fill_price=43.7,
                              closed_at=datetime(2026, 9, 25, 15, 1, tzinfo=timezone.utc),
                              trade_date=date(2026, 9, 25), reason="test")


def test_buy_counts_execution_but_pnl_changes_only_on_sell(tmp_path):
    account = PaperAccountStore(tmp_path / "paper.sqlite", default_starting_cash=1000)
    open_position(account)
    bought = account.snapshot().as_dict()
    assert bought["execution_count"] == bought["buy_count"] == 1
    assert bought["sell_count"] == 0
    assert bought["realized_pnl"] == 0
    close_position(account)
    sold = account.snapshot().as_dict()
    assert sold["execution_count"] == 2
    assert sold["buy_count"] == sold["sell_count"] == 1
    assert sold["realized_pnl"] == pytest.approx(-18.9)


def test_snapshot_cannot_mix_old_pnl_with_new_execution_count(monkeypatch, tmp_path):
    account = PaperAccountStore(tmp_path / "paper.sqlite", default_starting_cash=1000)
    open_position(account)
    original = account._connect
    closed = False

    def connect():
        connection = original()

        def on_query(query):
            nonlocal closed
            # Commit a sale between the account and counter reads.
            if not closed and "SELECT COALESCE(SUM(amount)" in query:
                closed = True
                close_position(account)

        connection.set_trace_callback(on_query)
        return connection

    monkeypatch.setattr(account, "_connect", connect)
    before = account.snapshot()
    assert closed
    assert before.trade_count == 0
    assert before.realized_pnl == 0
    assert before.open_positions == 1
    after = account.snapshot()
    assert after.trade_count == 1
    assert after.realized_pnl == pytest.approx(-18.9)
    assert after.open_positions == 0
