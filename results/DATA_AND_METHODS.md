# Data and Methods

## Shared implementation

- Sample: December 28, 2018 to March 31, 2026; 1,756 trading days and 87 complete calendar months from January 2019 to March 2026.
- Initial simulated capital: CNY 10 million.
- Rebalancing: monthly; signal formation followed by execution at the next trading day's open; sales precede purchases; positions leaving the target portfolio are exited.
- Main trading cost: 3 basis points on each purchase and sale; zero slippage; 252-day annualization; zero return on cash.
- Continuous shares are allowed. Round lots, minimum commissions, and size-dependent market impact belong to a future implementation assessment.

## Strategy identifiers

- `Pure_RRG_Q1`: up to six industries in the RRG leading quadrant; 220-day relative-strength lookback, 60-day relative-momentum lookback, and 20-day smoothing.
- `Report_base`: select the six industries with the highest breadth, then retain industries in the RRG leading or improving quadrants without replacement.
- `Industry_momentum_220_6`: select the six industries with the highest cumulative return over the prior 220 trading days.
- `ETF_equal_weight`: equal-weight the available ETF universe at each monthly rebalance.

The three active strategies share the same ETF-industry exposure mapping and portfolio optimizer. Portfolio weights sum to one, the single-ETF cap is one, and each selected industry's cap is `1/k`, where `k` is the realized number of selected industries. The optimizer maximizes exposure to selected signals. The number of selected industries therefore differs from the number of ETF holdings.

Parameter checks vary the return lookback across 180, 220, and 260 days and the maximum number of selected industries across 4, 6, and 8. The 60-day momentum lookback and 20-day smoothing window remain fixed.

## Data sources and comparability

Prices, historical industry membership, ETF metadata, holdings, and NAVs come from a user-authorized Tushare-compatible interface. Consistency with the official commercial Tushare distribution has not been independently established.

The source study is Western Securities, May 25, 2026, *Index Allocation Research (6): Sector Research under the Relative Rotation Graph Framework*. Its stated data sources are Wind and Gildata.

Reused computation functions come from `hugo2046/QuantsPlaybook`, pinned at commit `3f83a77f4c7996ed8185b8979a42c4372c1d811f`.

Final input database SHA256:

```text
e71a0043112ba173307eac16acbecc1f72c232bb1780ddac455d32c02a407524
```

## Interpretation conventions

- Historical industry membership covers 55.93% and 59.97% of stock-days in 2018 and 2019, representing 35.55% and 41.89% of free-float market capitalization. Supplemental queries add zero membership intervals. Market-cap coverage reaches 98.66% in 2020.
- Some ETF holding records contain only the ten largest positions. Undisclosed and unclassified shares remain explicit. Historical ETF categories and benchmark-index identities retain coverage gaps.
- Delisted ETFs are included. Under a pre-specified rule, the first subsequently announced month-end NAV estimates settlement proceeds, which move to cash on the next trading day after costs. The main equal-weight strategy contains 25 estimated settlements; the three active main strategies contain zero. Momentum settings 260/6 and 260/8 each contain one.
- The composite-versus-pure comparison changes industry screening, ranking, and the realized number of selected industries. It measures the performance difference between two complete strategies.
- Time, cost, and parameter exercises use the observed historical sample and are interpreted as within-sample stability evidence.
- Statistical tests use differences in monthly mean net returns. The main HAC lag is 3, with lags 1 and 6 as sensitivity checks. Paired circular block bootstraps use blocks of 3 and 6 months, 10,000 repetitions, and seed `20261107`.
- Holm adjustment covers 6 main comparisons, 24 cost comparisons, and 54 parameter comparisons in their respective test families. Benchmark-adjusted regression intercepts measure incremental performance beyond investable benchmarks.

## Core scripts

- `src/rrg_repair_inputs.py`
- `src/rrg_repair_backtest.py`
- `src/rrg_report_robustness.py`
- `src/rrg_report_inference.py`
- `src/rrg_sma10_overlay.py`

Raw databases remain outside the repository, and credentials are managed separately.

