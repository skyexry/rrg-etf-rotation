# Data requirements

The full backtest uses user-authorized data from a Tushare-compatible interface and a local DuckDB snapshot. Raw API responses, access credentials, and the large input database are stored outside this repository.

The main inputs are:

- industry-index prices and historical industry membership;
- A-share prices, adjustment factors, and free-float market capitalization;
- ETF metadata, prices, NAVs, and periodic-report holdings;
- historical prices or liquidation NAV records for delisted ETFs.

Scripts read from and write to `data/` and `results/` relative to the repository root. Interface credentials are supplied through `TUSHARE_TOKEN` or `TUSHARE_TOKEN_FILE`.

The input database used for the final results has SHA256:

```text
e71a0043112ba173307eac16acbecc1f72c232bb1780ddac455d32c02a407524
```

The hash identifies the research snapshot and does not grant redistribution rights. See [`../results/DATA_AND_METHODS.md`](../results/DATA_AND_METHODS.md) for coverage and implementation details.

