# Market processing and event routing

The engine maintains a separate set of tickers eligible for routine processing.
Closed, halted/error, and settlement-pending markets leave that loop after their
existing state checks. Their identity and books remain available to metadata,
lifecycle, quarantine, and settlement handlers, which execute before event
routing. This removes repeated historical-market database lookups without
removing evidence or preventing final settlement/recovery work.

Local book, trade, and lifecycle events skip unrelated idle markets only when no
scheduled evaluation is due and the market has not expired. Markets with an
active order or position are still checked: elapsed time can invalidate quotes,
make an IOC eligible, or trigger other checks even on another market's event.
Reference, metadata, heartbeat, and global health events retain their broader
routing. Eligible markets retain discovery order.

No strategy thresholds, decision cadence, raw capture, risk rules, execution
matching, or audit schema are changed. Rejection summaries retain their existing
counting semantics. Historical books and executor checkpoints are deliberately
preserved; this change is not memory-history deletion or storage retention.

## Validation

The 13,251-event incident replay was seeded with 96 closed historical markets.
The previous loop processed it in 29.64 seconds (~447 events/sec); the updated
loop took 1.49 seconds (~8,894 events/sec). Exact portfolio snapshots, execution
records, audit records, and raw compressed byte counts matched. Runtime ledger
body sizes were identical too. These are isolated replay measurements under
local host load, not verified sustained production throughput.

Regression tests check that 1,000 closed markets are not queried again on each
subsequent event, that local events retain due/position/expiration checks, and
that the original loop and routed loop produce equivalent records through entry,
exit, and final hold-to-settlement comparison. Generated UUID values are
normalized in the synthetic scenario comparison; the incident replay compares
its saved portfolio and audit values exactly.

Reproduce an aged replay with:

```sh
.venv/bin/python scripts/check_collector_throughput.py \
  --fixture data/runtime/throughput-burst.json \
  --checkpoint data/runtime/throughput-before-checkpoint.json \
  --config data/runtime/settlement-ioc-v1.json \
  --historical-markets 96 --output /tmp/market-routing.json
```

Baseline comparison requires the pre-change source; pass its `src` directory as
`PYTHONPATH` when generating the baseline, then use `--compare` for the updated
run. Both runs use isolated temporary ledgers, not the running bot's database.

With 1,000 historical markets, the updated replay took 1.50 seconds (~8,841
events/sec) and retained the same portfolio/audit results. A separate pair
without seeded history measured ~9,074 before and ~9,283 after. Timing varies
with workload and host conditions; the key change is avoiding work proportional
to the entire historical-market count on every incoming event.

The full regression suite passed: 689 tests. The update was deployed by a clean
restart of `settlement-ioc-v1`, with no open position or active order at restart.
Machine-readable results are saved in `data/runtime/market-routing-validation.json`.
