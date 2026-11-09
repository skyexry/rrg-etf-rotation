# RRG experiment and evidence package

Start with [`EXPERIMENT_SUMMARY.md`](EXPERIMENT_SUMMARY.md). It links each research question to the relevant evidence and interpretation.

| Directory | Contents |
| --- | --- |
| `01_results_tables` | Main performance, time stability, costs, parameters, statistical tests, and SMA comparison |
| `02_figures` | English PNG and PDF figures for direct review and reuse |
| `03_return_records` | Main NAV, cost/parameter scenario NAVs, monthly returns, candidate history, and settlement plan |
| `04_validation` | Run settings, input hashes, accounting checks, and statistical validation records |

[`DATA_AND_METHODS.md`](DATA_AND_METHODS.md) defines the shared settings, strategy identifiers, data sources, and research approximations.

Return fields are generally stored as decimals, so `0.2462` represents `24.62%`. Fields ending in `_cny` are denominated in Chinese yuan. Historical simulated profit includes unrealized gains and losses in the final portfolio.

