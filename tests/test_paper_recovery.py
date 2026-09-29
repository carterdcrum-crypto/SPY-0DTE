from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import pytest

from engine.ai_validation import AIValidationMixin
from engine.paper_account import PaperAccountStore
from engine.paper_autotrader import PaperAutoSettings
from engine.paper_hold_value_exit import HoldValueExitPolicyMixin
from engine import paper_runner
from engine.runtime import WorkerContext


def buy(account):
    return account.open_long(
        symbol="SPY260925C00670000", quantity=1, fill_price=50,
        opened_at=datetime(2026, 9, 25, 15, tzinfo=timezone.utc), entry_spot=670,
        strategy="test", stop_price=30, target_price=80, max_hold_seconds=600, reason="test",
    )


def test_overlapping_runners_cannot_trade_same_ledger(tmp_path):
    path = str(tmp_path / "paper.sqlite")
    with paper_runner.execution_lock(path):
        with pytest.raises(BlockingIOError):
            with paper_runner.execution_lock(path):
                pytest.fail("second runner acquired the same ledger")
    with paper_runner.execution_lock(path):
        pass


def test_recovery_after_committed_entry_does_not_buy_twice(monkeypatch, tmp_path):
    path = str(tmp_path / "paper.sqlite")
    monkeypatch.setenv("PAPER_DB_PATH", path)
    account = PaperAccountStore(path, default_starting_cash=115)
    context = WorkerContext()
    monkeypatch.setattr(context, "wait", lambda seconds: False)

    class CrashingTrader:
        settings = PaperAutoSettings()
        committed = False

        def tick(self):
            if not self.committed:
                buy(account)
                self.committed = True
            raise RuntimeError("crash after committed ledger write")

    monkeypatch.setattr(paper_runner, "create_trader", lambda *args, **kwargs: CrashingTrader())
    with pytest.raises(RuntimeError):
        paper_runner.run_forever(lambda: "PAPER", context, delayed=False)

    class RecoveredTrader:
        settings = PaperAutoSettings()

        def tick(self):
            restored = PaperAccountStore(path)
            assert len(restored.positions()) == 1
            assert restored.snapshot().buy_count == 1
            assert restored.snapshot().trade_count == 0
            context.stop.set()
            return {"state": "POSITION_OPEN"}

    monkeypatch.setattr(paper_runner, "create_trader", lambda *args, **kwargs: RecoveredTrader())
    paper_runner.run_forever(lambda: "PAPER", context, delayed=False)
    assert account.snapshot().buy_count == 1
    assert account.snapshot().trade_count == 0
    assert account.snapshot().settled_cash == 65


@pytest.mark.parametrize("delayed", [False, True])
def test_both_data_modes_use_latest_exit_and_validation(monkeypatch, tmp_path, delayed):
    monkeypatch.setenv("PAPER_DB_PATH", str(tmp_path / "paper.sqlite"))
    trader = paper_runner.create_trader(lambda: "SHADOW", delayed=delayed)
    assert isinstance(trader, HoldValueExitPolicyMixin)
    assert isinstance(trader, AIValidationMixin)


def test_concurrent_settlement_releases_cash_only_once(tmp_path):
    account = PaperAccountStore(tmp_path / "paper.sqlite", default_starting_cash=115)
    buy(account)
    account.close_long(symbol="SPY260925C00670000", quantity=1, fill_price=60,
                       closed_at=datetime(2026, 9, 25, 15, 1, tzinfo=timezone.utc),
                       trade_date=date(2026, 9, 25), reason="test")
    with ThreadPoolExecutor(max_workers=2) as executor:
        released = list(executor.map(account.settle_due, [date(2026, 9, 28)] * 2))
    assert sum(released) == 60
    assert account.snapshot().settled_cash == 125
