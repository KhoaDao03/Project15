# Collector throughput recovery — September 10, 2026

The IOC collector stopped when incoming traffic exhausted the 20,000-event queue.
The final status showed 13.4 seconds of processing lag. Captured traffic peaked
at 2,478 events/second. The inactive strategy flag was part of failure shutdown.

## Changes

Position monitoring now performs read-only exit checks before opening a database
transaction. Unchanged quotes do not copy the entire portfolio. Changes to extrema,
exit intents, fills and risk still use the existing atomic checkpoint/rollback path.
Exit-liquidity observation likewise writes only when consumed depth actually changes;
a closed market's historical consumption no longer triggers copies on every quote.
The immutable configuration hash is cached. Best-ask price and quantity are selected
without sorting the complete depth ladder twice on each evaluation.

Every source event is still recorded and applied in order. No order-book updates,
trade events, risk checks or exit checks are dropped or throttled. The queue limit
is unchanged. Status now includes current queue depth/capacity, and collector errors
in the combined dashboard include their full exception traceback.

## Validation on this host

* Full regression run: 680 passed. Later best-ask changes passed 133 targeted checks.
* Recorded interval: 13,251 authentic events, 05:27:00–05:27:30 UTC, with the
  incident's held position in an isolated ledger.
* Before: 13.23 seconds, about 1,002 events/second.
* After: 2.33 seconds, about 5,699 events/second, including compressed raw recording.
* Final portfolio snapshot and order/fill/result records matched exactly.
* Rollback tests cover failed checkpoints on extrema, exit and liquidity changes;
  unchanged-quote tests forbid both snapshot creation and database transactions.

The reproducible interval and results are local artifacts under `data/runtime/`:

```bash
uv run --locked python scripts/check_collector_throughput.py \
  --fixture data/runtime/throughput-burst.json \
  --checkpoint data/runtime/throughput-before-checkpoint.json \
  --config data/runtime/settlement-ioc-v1.json \
  --output data/runtime/throughput-after.json \
  --compare data/runtime/throughput-baseline.json --min-rate 5000
```

This benchmark measures engine processing and raw recording on the captured workload,
not every aspect of a live network/UI session or future traffic. The fixtures preserve
the same saved starting state for the before/after comparison; they do not rewrite the
real ledger. A short live health check and clean supervised restart also passed.
Neither constitutes a multi-day soak test.

## Installed services on this machine

`~/.config/systemd/user/btc15-ioc-collector.service` runs the existing `paper-service`
command with the frozen `settlement-ioc-v1` configuration and existing `data/btc15.db`.
`btc15-dashboard.service` serves the viewing dashboard at `127.0.0.1:8000` with
`--no-collect`. Both are enabled for the user-service lifecycle. User lingering is
enabled so they are not tied to a terminal/login session.

The collector restarts after a failure with a 30-second delay, at most three starts
within five minutes. The dashboard's delay is ten seconds. Clean shutdown does not
trigger automatic restart. Checkpoint compatibility, HALT/risk flags, writer ownership
and the 10 GiB disk reserve remain enforced. No automatic stale-lease deletion or
portfolio reset is installed. A hard kill or repeated failure can require recovery.

```bash
systemctl --user status btc15-ioc-collector btc15-dashboard
journalctl --user -u btc15-ioc-collector -n 100 --no-pager
uv run --locked btc15 --config data/runtime/settlement-ioc-v1.json paper-health --run-id settlement-ioc-v1

# Stop both safely:
systemctl --user stop btc15-ioc-collector btc15-dashboard

# Resume the existing run:
systemctl --user start btc15-ioc-collector btc15-dashboard
```

Dashboard **Shut down safely** still coordinates stopping the collector, saves
positions and closes the viewer. Do not launch a second dashboard collector with the
old foreground command while the service owns this database.

## Continuous-operation limits

This is WSL2. Windows/WSL must remain running and awake; user lingering does not
prevent host sleep, Windows shutdown or WSL termination. No Windows sleep-policy
change or external alert delivery was configured. Failures and health status are
available locally through the commands above. Monitor disk growth: source tapes are
retained, and the reserve deliberately stops collection before the disk fills.

Before calling this proven 24/7 operation, verify consecutive day-long sessions,
market rollover and settlement, bounded resource growth, UTC risk rollover, and
recovery from network and process interruptions using the same ledger. The previous
stranded paper position was reconciled through the normal official-settlement path;
its saved history and risk totals were preserved.
