# Distribution-first research contract

The research engine evaluates models by the probability distributions they produce, not by whether they emit a bullish or bearish label.

Required forward horizons begin with 5, 15, 30, 60 and 180 minutes. Each model must expose a normalized return distribution that can be scored out of sample with proper scoring rules and converted into strike/structure probabilities.

The default research scoreboard includes CRPS for the full distribution, Brier score for binary events such as finishing above a strike, calibration error, interval coverage, trading expectancy after executable costs, drawdown and tail-risk metrics.

Feature families are evaluated with paired block-bootstrap ablation. A paid feature or data source is not promoted because it sounds informative: it must improve untouched out-of-sample scores with a positive confidence interval and must separately justify its subscription cost relative to realistic trading capital.

No distribution model has authority to bypass stale-data, liquidity, settlement, drawdown or other hard risk vetoes.
