"""QuantConnect LEAN: isolated CASH-account SPY 0DTE V4/V5 challenger.

New QC project: copy this file to main.py plus v5_core.py. The repo's original
quantconnect/main.py and its unrelated spread-research V4 stay unchanged.
Run each 'variant' as a separate $1,000 QuantConnect backtest.
No Webull orders; fail closed on missing as-of options quote or size.
"""
from AlgorithmImports import *
from datetime import datetime
import json
import math

from v5_core import latest_signals, quality_score, contract_quantity, realized_volatility, ema


class PerContractFee(FeeModel):
    def get_order_fee(self, parameters):
        return OrderFee(CashAmount(.65 * abs(int(parameters.order.quantity)), "USD"))


class ExtraOptionSlippage:
    def get_slippage_approximation(self, asset, order):
        return 0.01  # Dollars/share, beyond actual ask-buy/bid-sell spread


class Spy0DteV5Comparison(QCAlgorithm):
    def initialize(self):
        self.set_time_zone(TimeZones.NEW_YORK)
        self.set_start_date(2024, 1, 2)
        self.set_end_date(2025, 12, 31)
        self.set_cash(1000)
        self.set_brokerage_model(BrokerageName.QUANTCONNECT_BROKERAGE, AccountType.CASH)
        self.variant = self.get_parameter("variant") or "v5_current"
        if self.variant not in ("v4_original", "v5_current", "v5_optimized",
                                "v5_unrestricted_research"):
            raise ValueError("unknown strategy variant")
        self.rvol_min = self.get_parameter("rvol_min")
        self.rvol_min = float(self.rvol_min) if self.rvol_min else None
        self.max_exposure = float(self.get_parameter("max_exposure") or .35)
        self.trailing = float(self.get_parameter("trailing") or
                              (.30 if self.variant in ("v4_original", "v5_current") else .25))
        if not .01 <= self.max_exposure <= .35 or not .10 <= self.trailing <= .50:
            raise ValueError("outside allowed risk parameter range")
        self.add_security_initializer(self._security_init)
        equity = self.add_equity("SPY", Resolution.MINUTE)
        equity.set_data_normalization_mode(DataNormalizationMode.RAW)
        self.spy = equity.symbol
        opt = self.add_option(self.spy, Resolution.MINUTE)
        opt.set_filter(lambda u: u.include_weeklys().expiration(0, 0).strikes(-10, 10))
        self.options = opt.symbol
        self.day = None
        self.series = []
        self.pending = None
        self.symbol = None
        self.exiting = False
        self.closed_for_day = False
        self.open_time = None
        self.premium_debit = 0.
        self.peak_bid_credit = 0.
        self.position_qty = 0
        self.cashflow = 0.
        self.realized = []
        self.daily = []
        self.intraday_peak = 1000.
        self.eod_peak = 1000.
        self.max_intraday_dd = 0.
        self.max_eod_dd = 0.
        self.no_quote = 0
        self.entries = 0
        self.signals = 0
        self.schedule.on(self.date_rules.every_day(self.spy),
                         self.time_rules.before_market_close(self.spy, 16),
                         self._liquidate_at_cutoff)

    def _security_init(self, security):
        if security.type == SecurityType.OPTION:
            security.set_fee_model(PerContractFee())
            security.set_slippage_model(ExtraOptionSlippage())

    def _liquidate_at_cutoff(self):
        self.closed_for_day = True
        self._sell("before_expiry_cutoff")

    def _sell(self, reason):
        if self.symbol is None or self.exiting:
            return
        qty = int(self.portfolio[self.symbol].quantity)
        if qty > 0:
            self.exiting = True
            self.market_order(self.symbol, -qty, tag=reason)

    def _day_start(self):
        if self.day == self.time.date():
            return
        if self.day is not None:
            self._record_eod()
        self.day = self.time.date()
        self.series = []
        self.pending = None
        self.closed_for_day = False

    def _record_eod(self):
        eq = float(self.portfolio.total_portfolio_value)
        self.daily.append(eq)
        self.eod_peak = max(eq, self.eod_peak)
        self.max_eod_dd = max(self.max_eod_dd, 1-eq/self.eod_peak)

    def _mark(self):
        eq = float(self.portfolio.total_portfolio_value)
        self.intraday_peak = max(eq, self.intraday_peak)
        self.max_intraday_dd = max(self.max_intraday_dd, 1-eq/self.intraday_peak)

    def _quote(self, data, symbol):
        q = data.quote_bars.get(symbol)
        if q is None or q.bid is None or q.ask is None or q.end_time != self.time:
            return None
        bid, ask = float(q.bid.close), float(q.ask.close)
        if bid <= 0 or ask <= bid:
            return None
        return (bid, ask)

    def _manage_position(self, data):
        if self.symbol is None or self.exiting:
            return
        quote = self._quote(data, self.symbol)
        if quote is None:
            self.no_quote += 1
            return
        bid, ask = quote
        credit = max(0., (bid-.01)*100-.65)
        self.peak_bid_credit = max(self.peak_bid_credit, credit)
        age = (self.time-self.open_time).total_seconds()/60
        if credit <= self.premium_debit*.65:
            return self._sell("hard_stop_35pct")
        trail = min(self.trailing, .20) if self.variant == "v4_original" and age >= 10 else self.trailing
        if credit <= self.peak_bid_credit*(1-trail):
            return self._sell("premium_trailing_stop")
        if age < 10:
            return
        if len(self.series) < 36:
            return self._sell("insufficient_completed_bars")
        closes = [x[0] for x in self.series]
        vols = [x[3] for x in self.series]
        direction = (1 if self.symbol.id.option_right == OptionRight.CALL else -1)
        momentum = direction * (closes[-1]/closes[-4]-1)*10000
        if self.variant == "v4_original":
            # Original frozen 10m extension gate; re-evaluated after 10m.
            rvol = vols[-1] / (sum(vols[-21:-1])/20)
            extend = (credit >= 1.10*self.premium_debit and
                      credit >= .90*self.peak_bid_credit and rvol >= .90 and
                      momentum >= 3 and direction*(closes[-1]-ema(closes[-36:],8))>0)
            if not extend:
                return self._sell("v4_conditional_10min")
        else:
            if momentum < 0:
                return self._sell("v5_underlying_reversal")
            if age >= (18 if self.variant == "v5_optimized" else 12):
                return self._sell("v5_time_exit")

    def _enter(self, data, signal):
        chain = data.option_chains.get(self.options)
        if chain is None or len(self.series) < 36:
            self.no_quote += 1
            return
        rv = realized_volatility([x[0] for x in self.series])
        if rv <= 0:
            return
        choices = []
        for c in chain:
            expiry = c.expiry.date() if isinstance(c.expiry, datetime) else c.expiry
            if expiry != self.time.date():
                continue
            if (signal.side == "call") != (c.right == OptionRight.CALL):
                continue
            quote = self._quote(data, c.symbol)
            if quote is None:
                continue
            bid, ask = quote
            spread = (ask-bid)/((ask+bid)/2)
            if spread > (.035 if self.variant == "v4_original" else .030):
                continue
            try:
                delta = float(c.greeks.delta)
                iv = float(c.implied_volatility)
                bid_size = int(c.bid_size)
                ask_size = int(c.ask_size)
            except (ValueError, TypeError, AttributeError):
                continue
            if (not all(map(math.isfinite,(delta,iv))) or iv <= 0 or
                iv/rv > 1.5 or not .40 <= abs(delta) <= .60 or
                min(bid_size,ask_size) <= 0):
                continue
            quality = quality_score(signal,spread,delta,iv,rv)
            qty = contract_quantity(
                self.variant,quality,float(self.portfolio.total_portfolio_value),
                float(self.portfolio.cash),self.intraday_peak,ask,
                bid_size=bid_size,ask_size=ask_size,
                maximum_exposure=self.max_exposure)
            if qty < 1 or qty*((ask+.01)*100+.65) > float(self.portfolio.cash)+1e-8:
                continue
            choices.append((abs(abs(delta)-.5),spread,str(c.symbol),
                            c.symbol,qty,ask))
        if not choices:
            self.no_quote += 1
            return
        _,_,_,symbol,qty,ask = min(choices)
        self.symbol = symbol
        self.open_time = self.time
        self.premium_debit = (ask+.01)*100+.65
        self.peak_bid_credit = 0.
        self.position_qty = 0
        self.cashflow = 0.
        self.exiting = False
        self.market_order(symbol,qty,tag=f"{self.variant}:{signal.setup}")
        self.entries += 1

    def on_data(self, data):
        t = self.time
        if not ((t.hour == 9 and t.minute >= 31) or (10 <= t.hour < 16)):
            return
        bar = data.bars.get(self.spy)
        if bar is None:
            return
        self._day_start()
        self._mark()
        if self.symbol is not None:
            self._manage_position(data)
        if self.pending is not None:
            signal, at = self.pending
            self.pending = None
            # No same-bar options lookahead. Only quotes observed one minute
            # AFTER closed signal, and only before session expiry cutoff.
            if (t-at).total_seconds()==60 and not self.closed_for_day and self.symbol is None and (t.hour,t.minute)<(15,20):
                self._enter(data,signal)
        self.series.append((float(bar.close),float(bar.high),
                            float(bar.low),float(bar.volume)))
        self.series = self.series[-80:]
        if (self.symbol is not None or self.exiting or self.closed_for_day or
            len(self.series)<36 or (t.hour,t.minute)<(9,52) or
            (t.hour,t.minute)>=(15,19)):
            return
        closes, highs, lows, vols = zip(*self.series)
        events = latest_signals(closes,highs,lows,vols,self.variant,self.rvol_min)
        if events:
            self.pending = (events[0],t)
            self.signals += 1

    def on_order_event(self, event):
        if event.status not in (OrderStatus.FILLED,OrderStatus.PARTIALLY_FILLED):
            return
        if self.symbol is None or event.symbol != self.symbol:
            return
        q = int(event.fill_quantity)
        if q == 0:
            return
        fee = float(event.order_fee.value.amount)
        self.cashflow -= q*float(event.fill_price)*100+fee
        self.position_qty += q
        if q > 0 and self.position_qty:
            self.premium_debit = -self.cashflow/self.position_qty
            self.peak_bid_credit = max(self.peak_bid_credit,self.premium_debit)
        elif q < 0 and self.position_qty == 0:
            self.realized.append(round(self.cashflow,2))
            self.symbol = None
            self.exiting = False
            self.open_time = None

    def on_end_of_algorithm(self):
        if self.day is not None:
            self._record_eod()
        wins = [x for x in self.realized if x>0]
        losses = [x for x in self.realized if x<0]
        from v5_core import score_equity
        equity = score_equity(self.daily)
        result = {
            "variant":self.variant,"starting_cash":1000,
            "sessions":len(self.daily),"equity":equity["ending_equity"] if equity else None,
            "sessions_to_5000":equity["sessions_to_5k"] if equity else None,
            "max_eod_dd_pct":round(100*self.max_eod_dd,2),
            "max_intraday_dd_pct":round(100*self.max_intraday_dd,2),
            "trade_count":len(self.realized),
            "win_pct":round(100*len(wins)/len(self.realized),2) if self.realized else None,
            "profit_factor":round(sum(wins)/abs(sum(losses)),3) if losses else None,
            "missing_or_rejected_quotes":self.no_quote,"closed_bar_signals":self.signals,
            "not_live_validated":True}
        self.log("V5_QC_RESEARCH_RESULT "+json.dumps(result,sort_keys=True))
