# SPY 0DTE: Option NET Edge Walk-Forward — Results (12 October 2026)

## Research verdict

**We can generate >5 simulated trades/day by combining independent
entry SETUP TYPES, but they are NOT independently PROFITABLE entry
strategies.** A per-contract forecast of P&L after option bid/ask
spread, fees, adverse slippage and additional $2 stress rejected most
trades. Its 33 selected hypothetical trades still lost money.
A conservative uncertainty-penalized profitability rule accepted
**zero trades**, not a purported zero-loss strategy.

This research DOES NOT identify a repeatable edge or a credible path
to reliably doubling account equity every week.

## What was actually coded and tested

Five entry experts are now pooled into a chronological candidate
stream:
1. Break of first **5-minute CLOSE** range.
2. Break of first **15-minute CLOSE** range.
3. Multi-completed-bar breakout → retest → directional confirmation
   of a 5-minute close range.
4. Same retest process for a 15-minute close range.
5. Original volume-confirmed legacy four-factor momentum, once its
   75 completed bars are available.

To examine MORE DAILY OPPORTUNITIES, this particular research sweep
permits post-morning entries up to **15:25 ET**. It **does not alter**
the prior morning-only 09:30–11:00 strategy setting. The broad
research policy uses a single simulated position at a time and
allows another entry only after a previous sampled exit.

Every option is valued at the modeled first eligible quote rather
than merely predicting which direction SPY goes. Twelve fixed
as-of feature components incorporate:
- SPY five- and 15-minute directional close changes, trend efficiency
  and relative volume;
- option ask premium, bid/ask spread, delta and
  option-implied vs market-realized volatility proxy;
- a **first-order delta-only estimated SPY move required to
  overcome the actual option spread, $0.65/side fees, 1¢/share/side
  slippage and an extra $2 per-trade stress**;
- time of day and option moneyness proxy.

**WARNING:** The delta-based required-move estimate is just a
cost hurdle, NOT a quantitative forecast of future SPY movement,
option repricing, IV change or premium P&L. Historical option net
P&L (not SPY direction) supplies the label.

A fixed-configuration, nearest-analog historical option-P&L
estimator trains on **PREVIOUS 45 COMPLETED trading sessions ONLY**,
with at least 20 prior labeled option trades per entry expert.
It weights 30 analogous historical candidates and shrinks
the forecast toward the previous expert's own payoff average.
The model never sees the outcomes of its trading day while
scoring its decisions.

We evaluate five predeclared policies, each with identical exits:
- all qualifying setup candidates, one position at a time;
- an entry cost hurdle alone (estimated observed 5-minute
  SPY movement at least 1.5× first-order delta break-even and
  option spread <=8%);
- analog estimated historical option P&L **after** extra $2 friction > 0;
- analog positive net expectation AND cost hurdle;
- analog positive EV AND uncertainty-penalized lower bound > 0.

### Exits remain FIXED ACROSS research arms

**30% whole-position high-water trailing exit**;
independent 35% initial premium-loss stop; maximum 10-minute hold;
**no fixed profit target, no partial sales, no runner**.
Historical option samples are minute-bucket CBBO, with modeled
ask-entry/bid-liquidation and $0.65/side and 1¢/share/side
adverse execution slippage, plus an additional separate
$2/roundtrip stress. They are NOT actual executable NBBO fills.

## Actual run: 128 inspected sessions; 83 after 45-day warmup

The experiment found **1,114** candidate option quote/path
examples with modeled option outcomes. Of these, entry-expert
candidate counts were 304 direct 5m, 293 direct 15m,
73 retest 5m, 65 retest 15m, and 379 legacy four-factor.
Opportunities overlap; the simulator takes one open trade at
a time. Rates below are **trades per calendar market session,
including no-trade days**.

| Policy (83 post-warmup sessions) | Completed one-contract scenarios | Trades/day | Win rate | Sum modeled 1-contract P&L | Extra $2/trade stress |
|---|---:|---:|---:|---:|---:|
| ALL FIVE EXPERTS, no entry profit gate | **443** | **5.337** | 32.73% | **-$2,745.90** | -$3,631.90 |
| Cost hurdle only | **376** | **4.530** | 32.45% | **-$2,642.80** | -$3,394.80 |
| Prior analog estimated net P&L positive | 33 | 0.398 | 42.42% | -$228.90 | -$294.90 |
| Prior analog positive + break-even cost hurdle | 33 | 0.398 | **42.42%** | **-$207.90** | **-$273.90** |
| **Uncertainty-penalized estimated net P&L positive** | **0** | **0** | — | **$0 / NO TRADES** | $0 |

The multi-entry unfiltered variant traded on 82 of 83 days,
had at least two simulated entries on all 82 active days,
had at least three on 78 days, and hit **15 entries in one
day**. **That higher frequency significantly worsened
total P&L**, and is not automatically possible with
limited cash-account settled buying power.

The 33 hypothetical entries admitted by the net-P&L
model won 14 and lost 19: ~42.4% accuracy. Still negative:
- Original positive-net gate: -$228.90 gross modeled net;
- Positive-net plus cost hurdle: -$207.90;
- Cost-stressed variant: -$273.90.
- The plus-cost variant had **9 positive days and 12 negative
  days**, across 21 active days in its 83-calendar-session window.
- The extra friction five-session-block uncertainty interval
  for per-market-session P&L was **[-$12.104, +$4.076]**
  and therefore includes zero. This is descriptive ONLY,
  not a true holdout or a correction for repeated strategy
  selection.
- The 33 admitted trades came from **27 postwarmup development
  trades (-$79.10)** and **six validation trades (-$128.80)**;
  the 21 previously inspected diagnostic sessions produced
  **zero admitted trades**.

Our best-supported finding remains **there is no validated
positive expected net option profit in this data**. A
strong-looking probability of winning is not the same as
recovering the spread and fees under real execution.

## Independent direct market-data audit executed with the test

Research service:
- Archived minute-CLOSE/CBBO files: **128**.
- True SPY 1-minute **OHLCV** files: **0**.
- Independently event-observed SPY 0DTE bid/ask+size files: **0**.
- Paired genuine event-time sessions usable for fresh ORB/VWAP/
  realistic option fill study: **0**.

We already have strict input schemas and an as-of loader in
`engine/weekly_options_data.py`, plus free metadata-only
vendor cost/readiness preflight in
`engine/spy_event_data_preflight.py`.

**No market-data downloads were purchased**, and there were
**no market orders**. We cannot claim to have completed genuine
OHLC high/low, trade-time NBBO or prospective option-fill
backtesting, because those data don't exist on the connected
research volume today.

## Next actual research milestone

**Freeze the net expected option-P&L scoring policy and set up
genuinely prospective paper evidence** when real timestamped
completed SPY OHLCV and same-session 0DTE executable option
bid/ask/size observations are available. Log EVERY opportunity,
accepted and declined, with predicted net P&L, quote age, spread,
ask debit, settled-cash affordability, real paper fill assumption,
full intraday outcome and transaction costs. Then measure net
expected return, payoff asymmetry, maximum peak-to-trough ACCOUNT
drawdown, coverage by distinct calendar week, percentage of
profitable days, and actual opportunities per day. Compare
against a frozen no-trade control and current broad baseline.

Without real point-in-time quotes and a untouched forward sample,
**do not promote these entries to autonomous cash trading**.
More candidates are only useful after independent net
profitability is established.

## Limitations

- The 128-session historical set was viewed and used repeatedly
  in prior experiments. Chronologically fitting prior days
  prevents direct training leakage but is NOT fresh OOS
  validation or multiple-hypothesis correction.
- Historical CBBO quotes are minute buckets, NOT event-time
  verified executable NBBO or actual spread/size at a precisely
  observed moment. Gamma, theta, IV changes and intraminute
  stop execution create additional real-world discrepancies.
- Close-based opening range is NOT true completed OHLC highs/
  lows or volume-weighted average execution.
- Some markets lacked realistic option-trace observation;
  the research uses only complete sampled paths to label
  candidate profits, and reserves a ten-minute slot when
  future quotes go missing after hypothetical admission.
- Put/call full-chain point-in-time SPY PCR remains unavailable.
- This is **one hypothetical contract** per research trade,
  not a funded T+1 settled-cash account simulation. Increasing
  attempted daily frequency does not imply that affordable
  trades exist at a small cash balance.
- A 100% **weekly account growth** goal is extremely risky and
  not remotely supported by these data or algorithm tests.

## Reproducibility

```bash
python -m pytest -q tests/test_option_net_edge_walkforward.py
python -m engine.option_net_edge_walkforward \
    --data-dir /data/research \
    --output-dir /data/research/option_net_edge
```

Commit branch `research/option-net-edge-walkforward-20261012`.
Artifacts:
- `engine/option_net_edge_walkforward.py`
- `tests/test_option_net_edge_walkforward.py`
- `research/results/option_net_edge_walkforward_2026_10_12.json`
- `/data/research/option_net_edge/trades.csv` (research
  Railway volume, not a GitHub file)
- `engine/weekly_options_data.py` strict timestamped event-input
  contracts and `engine/spy_event_data_preflight.py` metadata
  inventory/cost-only readiness tools.

**Production / app / live automation unchanged; zero broker orders.**
