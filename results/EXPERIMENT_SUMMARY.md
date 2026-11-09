# RRG Experiment Summary

Final unified ETF-universe results, November 7, 2026.

## 1. Main comparison after trading costs

| Strategy | Annualized return | Maximum drawdown | Simulated profit (CNY 10,000) |
| --- | ---: | ---: | ---: |
| Pure RRG Leading Quadrant | **24.62%** | -44.76% | **3,635.65** |
| Breadth + RRG | 18.95% | **-38.40%** | 2,351.09 |
| Industry Momentum | 21.83% | -46.19% | 2,958.39 |
| ETF Equal Weight | 10.12% | -38.78% | 957.25 |

The pure leading-quadrant strategy has the highest return in the main setting. It exceeds industry momentum by 2.79 percentage points annualized and generates CNY 6.77 million more cumulative simulated profit, while accepting a deeper drawdown than the composite strategy.

Evidence: `01_results_tables/main_performance.csv` and Figure 1.

## 2. Performance over time

Across 76 overlapping rolling 12-month windows, pure RRG exceeds momentum 47 times, the composite strategy 53 times, and equal weight 55 times. It ranks first in 29 windows. Annual returns are -16.47% in 2022 and -16.55% in 2023.

The advantage spans multiple windows while including sustained loss periods. Overlapping windows are descriptive rather than independent observations.

Evidence: `annual_returns.csv`, `rolling_summary.csv`, `rolling_12m_returns.csv`, and Figures 5–6.

## 3. Dependence on the best months

The best three months account for 34.92% of the pure-RRG strategy's total log growth. In a mechanical stress test that replaces each strategy's own three best monthly returns with zero, annualized returns are 15.40% for pure RRG, 13.67% for momentum, 9.75% for the composite strategy, and 2.61% for equal weight.

The best months contribute materially, while strategy ordering survives this retrospective stress test. This diagnostic is not an executable trading rule.

Evidence: `month_dependency.csv`.

## 4. Trading-cost sensitivity

Pure RRG exceeds industry momentum at one-way costs of 3, 5, 10, and 20 basis points. At 20 basis points, annualized returns are 21.49% for pure RRG, 19.27% for momentum, 15.91% for the composite strategy, and 9.80% for equal weight.

The historical return ranking is preserved across the tested fixed-cost range. Market impact has not been calibrated to a specific portfolio size.

Evidence: `cost_sensitivity.csv` and Figure 2.

## 5. Parameter robustness

The grid combines lookbacks of 180, 220, and 260 trading days with maximum industry counts of 4, 6, and 8. Pure RRG exceeds same-parameter momentum in 6 of 9 settings, the composite strategy in 7 of 9, and equal weight in all 9. Its annualized return ranges from 12.73% to 24.62%.

Multiple settings remain economically attractive, while rankings and magnitudes vary. The 220-day/six-industry main setting is the highest pure-RRG result in the tested grid.

Evidence: `parameter_grid.csv` and Figure 3.

## 6. Statistical uncertainty

Using 87 complete months, the HAC(3) test gives `p=0.468` for pure RRG versus momentum. Pure RRG versus equal weight has raw `p=0.017` and Holm-adjusted `p=0.101` within the six main comparisons. A three-month paired block bootstrap gives a 95% interval of `[-4.85, 10.76]` percentage points for the annualized difference versus momentum.

The sample supports a historical economic advantage; evidence for a persistent incremental return remains weak. Full cost, parameter, and HAC sensitivity results are retained.

Evidence: `primary_pairwise_tests.csv`, `all_HAC_tests.csv`, `paired_block_bootstrap.csv`, and Figure 4.

## 7. Fixed ten-month moving-average overlay

With the filter, pure RRG returns 16.34% annualized with a -47.04% maximum drawdown. Filtered momentum returns 13.16% with a -53.96% drawdown.

This fixed rule reduces returns and deepens drawdowns in the observed sample, so the unfiltered strategies remain the main specification. The result is retained as a transparent risk-control counterexample.

Evidence: `sma10_performance.csv`, `sma10_monthly_comparison.csv`, and Figure 7.

## 8. Comparison with the source report

The source report states 21.51% annualized return and -34.98% maximum drawdown for its composite ETF strategy. This reconstruction produces 18.95% and -38.40%, respectively: 2.56 percentage points lower annualized return and 3.42 percentage points deeper drawdown.

Data coverage, the ETF universe, holding disclosures, and implementation details differ. The gap is therefore reported as reconstruction error rather than a single causal effect. The pure leading-quadrant portfolio is an extension of the source report's application.

All figures use the same revised ETF universe and sample. See [`DATA_AND_METHODS.md`](DATA_AND_METHODS.md) for shared conventions.

