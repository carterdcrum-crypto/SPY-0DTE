# SPY — score direction AND movement size (joint probability experiment)

**Research-only, $0 in purchased market data, adaptive trade filtering remains OFF.**

## Question and hypothesis

The prior 13/7 confirmed continuation research found that many predictions
were directionally right but moved SPY only a fraction of the way needed
to overcome **hypothetical underlying** costs. The next relevant question
is therefore not just "up or down?", but **how likely is a move in the
correct direction larger than 2bps or 5bps?**

These are **NOT real SPY 0DTE option break-even thresholds**. Option bid/ask
spreads, contract sizing, gamma, theta, volatility and brokerage fees are
unknown, so this study never simulates option trades or account P&L.

## Frozen model (no parameter optimization)

Start with the identical 15-prior-**close** SPY breakout opportunities
and the three-minute causal confirmation protocol used in previous
research. At the end of signal minute T+3, use only data known then:

- Was the previously frozen three-minute continuation rule satisfied?
- Does the causal rolling thirty-completed-minute volatility estimate
  predict a ten-minute **absolute SPY price move >=5 basis points**?

Those two facts define **four** immutable feature groups. Every opportunity
has the same hypothetical SPY **entry proxy** of the **next completed
one-minute close (T+4)** and exit after another ten complete minutes
(T+14). The outcome is a *signed SPY underlying price change* in bps,
**not an executable price or option trade**.

Before scoring session D, use **only the previous 60 complete trading
sessions**, with an initial warm-up of at least 40 completed sessions
and at least 100 historical signal events. This is a rolling
**observed past-session outcome forecast**, not a trade-admission
adaptive signal filter. All data, including earlier losing events and
events the old filters would have skipped, count as historical evidence.
No labels from D are used until the next complete session begins.

For each forecast emit joint/ordered binary probabilities of:
1. Signed SPY move **>0 bps** (direction right).
2. Signed SPY move **>2 bps** in the chosen direction.
3. Signed SPY move **>5 bps** in the chosen direction.
4. Signed SPY move **<−5 bps** (material adverse direction).

The controls are:
- **Lagged unconditional base rate** of all prior events, with a fixed
  Beta(1,1) smoothing prior.
- **Lagged two-feature conditional frequency**, shrunk toward the
  matching-date unconditional baseline using **40 pseudo-observations**.
  No coefficients are tuned to the archive; tiny continuation groups
  are automatically heavily shrunk toward the baseline.

All predictions occur before the current-session outcome. The model
is NEVER connected to a live executor or trade selection.

## Exact predeclared evaluation

Brier score (mean squared forecast probability error; **lower is better**)
separately for each of the four outcomes, unconditional vs conditional
on the **same session/timestamp/opportunity**. Report each period's
event count and actual outcome incidence, and **conditional minus baseline
Brier difference**. An improvement is a **negative** delta.

Use a 1,000-replicate seeded paired **circular 5-trading-session**
block bootstrap of **daily mean score differences**, including
zero-event and warmup days as zero. These intervals are descriptive
and cannot make this pre-inspected dataset into an independent holdout.

Report fixed ten probability bins (0.0–0.1, …, 0.9–1.0) with each
group's average forecast vs observed incidence, explicitly flagging
cells with fewer than twenty events. Store complete per-event
predictions, prior training counts, prior sub-group counts and
AFTER-THE-FACT realized outcome labels. Never use these realized
labels to filter the same-day model.

Chronological diagnostic partitions match earlier research:
Apr–Jul 2026 development; Aug 3–Sep 4 validation; Sep 8–Oct 6
**previously studied diagnostic**. ALL dates have been examined before,
so none is a truly untouched holdout. The model is NOT eligible for
automatic options trading based on these results.

**Predeclared go/no-go:** the conditional model should improve Brier
for both >2 and >5 basis-point signed targets across at least the
Aug–Sep and Sep–Oct historical partitions, with supportive paired
uncertainty, before investing more in this direction. Even then
this is only a SPY underlying forecast calibration improvement; real
0DTE net P&L requires timestamped options quotes, fees and liquidity,
plus future untouched sample testing.

## Reproduce without buying data

```bash
python -m pytest -q tests/test_joint_probability_audit.py
python -m engine.joint_probability_audit \
  --data-dir /data/research \
  --output-dir /data/research/joint_probability
```

Isolated Railway research-only service `spy-0dte-research`:
`RESEARCH_MODE=jointprobability`. The existing licensed historic
volume persists:
- `joint_probability/summary.json`
- `joint_probability/predictions_AND_future_outcome_labels_NOT_OPTION_TRADES.csv`
- `joint_probability/fixed_probability_bins.csv`

No data purchases, no Webull API or broker order, no Auto Trade toggle
change; research hosting still consumes existing Railway resources.


## Completed archived research results — October 9, 2026

Railway deployment `7d33bc20-2165-4b67-b8a3-5857af34cb13`
executed successfully using **128 previously licensed SPY sessions,
49,792 observed historical 1-minute snapshots**, and produced
**1,704 strictly past-trained forecast rows**. The first 40 sessions
and periods with insufficient prior events were warmup, not fake
predictions. No new historical data was purchased; no Webull orders
or other broker-side calls were made. The adaptive signal filter
remains OFF.

### Main preregistered comparison: conditional minus unconditional Brier

Both probabilities were trained exclusively on up to 60 earlier
completed SPY trading sessions and scored on the **same
next-completed-bar 10-minute SPY directional price proxy outcomes**.
Lower Brier is better; **negative delta means the two-feature
joint signal was more accurate as a probability forecast**.
Numbers are **not account P&L and not probabilities of profitable
options contracts**.

| Chronological period | Target signed SPY price move | Forecasted events | Lagged baseline Brier | Two-feature conditional Brier | Conditional − baseline | 95% paired 5-day bootstrap difference interval |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Development Apr–Jul | **>2bps** | 813 | 0.237851 | **0.236248** | −0.001602 | [−0.001504, −0.000170] |
| Validation Aug–Sep | >2bps | 497 | **0.231751** | 0.232647 | **+0.000896** | [−0.001695, +0.003586] |
| Already-studied Sep–Oct | >2bps | 394 | 0.227438 | **0.225698** | −0.001740 | [−0.003808, +0.000119] |
| Development Apr–Jul | **>5bps** | 813 | 0.194506 | **0.189733** | −0.004773 | [−0.003947, −0.000975] |
| Validation Aug–Sep | **>5bps** | 497 | 0.147189 | **0.142492** | **−0.004697** | **[−0.007713, −0.001075]** |
| Already-studied Sep–Oct | **>5bps** | 394 | 0.139905 | **0.135328** | **−0.004577** | **[−0.008829, −0.001140]** |
| Validation Aug–Sep | **<−5bps** adverse | 497 | 0.127603 | **0.115651** | **−0.011952** | **[−0.019141, −0.004382]** |
| Already-studied Sep–Oct | **<−5bps** adverse | 394 | 0.145367 | **0.141105** | **−0.004262** | **[−0.008632, −0.000221]** |

For completeness: predicting merely the *correct direction*
(>0bps) showed **no robust gain**. Conditional-minus-base Brier
for >0bps was +0.000276 in development, −0.000419 validation
and +0.000349 later. Both validation and later confidence intervals
included zero. The conditional model also failed to improve
the **>2bps** goal consistently in the final two periods.

### What the research supports — and what it does not

**Measurable, narrow progress:** adding the as-of-confirmation
continuation flag and expected movement category helped forecast
the frequency of **fairly large (>|5|bps), signed SPY-price
moves** beyond a lagged unconditional base rate on *previously
inspected* historical sessions. For >5bps correct-direction moves,
the mean Brier decreased approximately 3.2% in Aug–Sep and
3.3% in Sep–Oct versus the lagged base rate. Adverse move
calibration also improved in both periods.

**Still not a winning strategy:** these are probabilistic forecast
scoring improvements, not realized trading returns, not actual
SPY 0DTE premiums, and not evidence of sustainable 100% weekly
account gains. The original confirmed continuation trades remained
negative in the later historical period, and the relevant
>2bps probability showed no consistent validated gain. **We
have NOT used these forecast probabilities to trigger orders or
relaxed any financial risk guardrails.**

**Multiple-testing and past inspection caveat:** the same archived
dates have been viewed in prior rounds. The 5-day block intervals
are descriptive and do not make any date an untouched holdout.
This two-feature combination and its priors are deliberately fixed
for any subsequent untouched evaluation. Avoid choosing a new
feature or threshold on these same outcomes then calling it
prospective evidence.

**Next evidence gate:** keep the source frozen, gather future
untouched underlying signals and event-time observed SPY 0DTE
option quote prices/size. Analyze actual call/put entry-ask,
exit-bid, fees, slippage, expiry, cash-settlement and account
drawdown **before** using any probabilistic model for live orders.
Any purchased market data must have explicit authorization.
