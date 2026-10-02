from datetime import timedelta


class VirtualSpreadLedger:
    def __init__(self, name, starting_cash=100.0, fee_per_leg_each_side=0.65):
        self.name = name
        self.balance = float(starting_cash)  # available cash
        self.starting_cash = float(starting_cash)
        self.fee = float(fee_per_leg_each_side)
        self.position = None
        self.last_exit_time = None
        self.trades = 0
        self.wins = 0
        self.gross_profit = 0.0
        self.gross_loss = 0.0
        self.unaffordable_signals = 0
        self.no_contract_signals = 0
        self.high_water = float(starting_cash)
        self.max_drawdown = 0.0

    def can_enter(self, now, cooldown_minutes):
        if self.position is not None:
            return False
        if self.last_exit_time is None:
            return True
        return now - self.last_exit_time >= timedelta(minutes=cooldown_minutes)

    def max_entry_cost(self, max_premium_fraction, max_capital_fraction):
        if self.balance <= 0.0:
            return 0.0
        return min(
            self.balance * float(max_premium_fraction),
            self.balance * float(max_capital_fraction),
        )

    def open(self, pair, now):
        long_contract, short_contract = pair
        debit = max(
            0.0,
            (float(long_contract.ask_price) - float(short_contract.bid_price)) * 100.0,
        )
        if debit <= 0.0:
            return False

        entry_fees = 2.0 * self.fee
        total_entry_cost = debit + entry_fees
        if total_entry_cost > self.balance + 1e-9:
            return False

        # Cash-account accounting: pay the spread debit and entry fees immediately.
        self.balance -= total_entry_cost
        self.position = {
            "long": long_contract.symbol,
            "short": short_contract.symbol,
            "entry_debit": debit,
            "entry_time": now,
            "entry_fees": entry_fees,
        }

        # Mark the just-opened position at immediately executable liquidation prices
        # so bid/ask slippage appears in drawdown from the moment of entry.
        immediate_credit = max(
            0.0,
            (float(long_contract.bid_price) - float(short_contract.ask_price)) * 100.0,
        )
        exit_fees = 2.0 * self.fee
        marked_equity = self.balance + max(0.0, immediate_credit - exit_fees)
        self._update_drawdown(marked_equity)
        return True

    def _mark_credit(self, quotes):
        if self.position is None:
            return None
        long_quote = quotes.get(self.position["long"])
        short_quote = quotes.get(self.position["short"])
        if long_quote is None or short_quote is None:
            return None
        long_bid = float(long_quote.bid_price)
        short_ask = float(short_quote.ask_price)
        if long_bid < 0.0 or short_ask < 0.0:
            return None
        return max(0.0, (long_bid - short_ask) * 100.0)

    def mark_and_maybe_exit(
        self,
        quotes,
        now,
        stop_loss_fraction,
        take_profit_fraction,
        max_hold_minutes,
        force_exit=False,
    ):
        if self.position is None:
            self._update_drawdown(self.balance)
            return None

        credit = self._mark_credit(quotes)
        if credit is None:
            return None

        entry_debit = float(self.position["entry_debit"])
        entry_fees = float(self.position["entry_fees"])
        exit_fees = 2.0 * self.fee
        pnl = credit - exit_fees - entry_debit - entry_fees
        base = max(1e-9, entry_debit + entry_fees)
        trade_return = pnl / base

        # balance is cash after the original debit was paid; add only current
        # executable liquidation proceeds to obtain marked account equity.
        marked_equity = self.balance + max(0.0, credit - exit_fees)
        self._update_drawdown(marked_equity)

        held = now - self.position["entry_time"]
        should_exit = (
            trade_return <= -float(stop_loss_fraction)
            or trade_return >= float(take_profit_fraction)
            or held >= timedelta(minutes=max_hold_minutes)
            or force_exit
        )
        if not should_exit:
            return None
        return self._close(credit, now)

    def _close(self, credit, now):
        entry_debit = float(self.position["entry_debit"])
        entry_fees = float(self.position["entry_fees"])
        exit_fees = 2.0 * self.fee
        proceeds = max(0.0, float(credit) - exit_fees)
        pnl = proceeds - entry_debit - entry_fees

        # Return liquidation proceeds to cash. The original debit was already
        # removed at entry, so adding P&L again here would double-count it.
        self.balance += proceeds
        self.trades += 1
        if pnl > 0.0:
            self.wins += 1
            self.gross_profit += pnl
        elif pnl < 0.0:
            self.gross_loss += -pnl
        self.position = None
        self.last_exit_time = now
        self._update_drawdown(self.balance)
        return pnl

    def _update_drawdown(self, equity):
        equity = max(0.0, float(equity))
        self.high_water = max(self.high_water, equity)
        if self.high_water > 0.0:
            self.max_drawdown = max(
                self.max_drawdown,
                1.0 - equity / self.high_water,
            )

    def metrics(self):
        ending_equity = self.balance
        growth = ending_equity / self.starting_cash - 1.0
        win_rate = self.wins / self.trades if self.trades else 0.0
        if self.gross_loss > 0.0:
            profit_factor = self.gross_profit / self.gross_loss
        elif self.gross_profit > 0.0:
            profit_factor = float("inf")
        else:
            profit_factor = 0.0
        return {
            "ending_equity": ending_equity,
            "profit_dollars": ending_equity - self.starting_cash,
            "growth_pct": growth * 100.0,
            "trades": self.trades,
            "trade_win_rate_pct": win_rate * 100.0,
            "profit_factor": profit_factor,
            "max_drawdown_pct": self.max_drawdown * 100.0,
            "unaffordable_signals": self.unaffordable_signals,
            "no_contract_signals": self.no_contract_signals,
        }
