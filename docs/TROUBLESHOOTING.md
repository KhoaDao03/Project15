# Troubleshooting — especially zero paper trades

A no-trade result can be valid. Diagnose the stage that stopped progress before changing thresholds or resetting data. This guide describes the single-strategy branch; older dashboard/strategy-group behavior is not assumed.

## Establish what is actually running

From the running checkout's root, record `git branch --show-current` and `git rev-parse HEAD`, the exact startup command, run ID, configuration hash and database/data paths. Pulling code does not update an already-running Python process. The branch should contain the fractional-fill correction and single-strategy scope; do not assume `main` is equivalent.

Use the normal Settlement Edge history scope, the correct PAPER/BACKTEST mode, and the intended run. `dashboard --observe-only` and `collect` do not submit paper orders; `dashboard --no-collect` needs a separate writer. Default `dashboard` starts paper execution only after its startup checks succeed.

The default storage pair is `DATA_DIR=data` plus `DATABASE_URL=sqlite:///data/btc15.db`. The supplied systemd template instead uses `data/production-paper/paper.db`. A dashboard pointed at another database can look empty without anything having been deleted. Ports and history filters do not change database selection.

## Find the failed stage

| Evidence | Interpretation / next check |
| --- | --- |
| Collector startup error or no fresh status | Check credentials/key path, available space, lease, config mismatch and legacy recovery guards |
| Current evaluations, zero submitted orders | Inspect effective configuration, rejection reasons, enabled state and operational gates |
| Orders and cancellations, zero buy fills | Check latency, matching side/price, opposing volume, queue ahead, signal invalidation and timeout |
| Buy fills, no completed trade | Check retained open positions, exit eligibility and received official settlement |
| Zero entry-evidence rows | Not a diagnosis: compact paper mode archives opportunity evidence only on first fill |

Use **Trade history → Orders / cancellations**, **Fills**, **Completed trades**, and **Entry evidence** separately. Exceptional health records can be inspected via `/api/records?kind=health&mode=PAPER&run_id=RUN_ID`. Replace `RUN_ID` in requests. `/api/health` includes collector freshness and startup failure information. Console logs contain the underlying startup exception; the UI may show only a generic error.

For a named managed run:

```bash
uv run --locked btc15 paper-health --run-id settlement-original
uv run --locked btc15 analytics --mode PAPER --run settlement-original
```

Use the intended database environment/global override in the terminal. Health exit 1 means its freshness/operating criteria failed; it does not identify a bad strategy signal. Analytics rejection counts cover retained opportunities, not all compact-mode skipped evaluations.

## Common startup failures

**Private-key file missing:** `.env.example` points at a placeholder file. Save the real private key there, correct the path, or leave both credential fields empty for offline/public checks. Never publish the key. Public `discover` is not a WebSocket entitlement test.

**Writer owns this database:** stop/review the original writer. `--no-collect` is the correct viewer flag beside a collector. Never clear a live lease or start a second writer on another port to bypass this. After a genuine crash, follow [Safety](SAFETY.md#crash-recovery).

**Checkpoint configuration/mode mismatch:** use the old frozen file and original run. A Settings save or a moderate preset does not convert an existing portfolio. Compare the run/evaluation config hash, not just today's settings form. Do not overwrite the frozen file.

**Retired multi-strategy portfolio / unresolved archived exposure:** the old group/child needs recovery using the pinned compatible revision. See [SINGLE_STRATEGY.md](SINGLE_STRATEGY.md). Zero completed trades is not proof that there are no pending orders or positions.

**Free disk below reserve:** dashboard paper/managed operation reserves 10 GiB on the data filesystem. Archive/back up recordings safely and provision space; do not erase trade history or weaken the guard just to start. The guard does not monitor a remote PostgreSQL server's storage.

**LIVE DISABLED:** keep `TRADING_MODE=PAPER` and `ENABLE_LIVE_TRADING=false`. Changing flags is not a supported way to test this bot.

**Port in use:** close the old UI or choose another port for a viewing dashboard. This does not solve a writer conflict. A closed browser tab is not a stopped backend process.

## Common signal and execution blocks

`ENTRY_WINDOW` reports seconds **remaining**, not elapsed. Original: `120 < remaining <= 480`; moderate: `120 < remaining <= 600`. Therefore `545.962` is too early for the original control but passes moderate timing. Evaluate reasons inside the actual eligible window to avoid letting expected early rejections dominate analysis.

`MIN_EDGE` / `MIN_EV`: both gate conservative probability minus ask, conservative fee estimate and slippage. High win probability or an ask inside the price limits is not enough. Inspect the actual adjusted values; do not remove the cost or uncertainty deduction to force trades.

`MODEL_UNAVAILABLE`, low quality or warmup reasons: verify sufficient standard reference samples and continuity. A moving 5 Hz price display is not the model's required settlement-reference stream.

`STALE_REFERENCE`, `STALE_BOOK`, `PROCESSING_LAG`, `CLOCK_SKEW`, `FEED_UNHEALTHY` or unverified fee metadata: inspect the capture/logs and host/network health. The engine rechecks execution freshness after model computation, independently of display updates. Do not substitute another exchange's price or disable the check.

`RISK_LIMIT`, `EXISTING_ENTRY`, `KILL_SWITCH`: an earlier unfilled attempt still consumes daily attempts/gross exposure, and an attempted market cannot be retried within that run. Daily buckets use UTC. HALT can remain latched in the checkpoint. A new name/database is not a supported risk reset.

`Invalid fill quantity`: confirm the running checkout includes the Decimal queue fix, preserve the triggering tape and nearby health/order rows, and distinguish internal arithmetic from genuinely invalid source volume. The fix does not repair previously cancelled orders or retroactively rewrite results.

`signal_invalid_or_timeout`: this generic cancellation reason covers several conditions. Inspect the original order, contemporaneous inputs and current signal; it does not prove which particular condition was responsible. Quote touching the limit does not prove a passive fill.

## Evidence needed for an actual-data investigation

Preserve the exact revision and frozen config, run/order/fill/result/health records, checkpoints and the complete ordered `.jsonl.gz` input tapes including needed warmup/predecessor sessions. Back up consistently. Do not share credentials/private keys or rewrite received timestamps. A single latest evaluation cannot explain an entire night.

Replay into a separate BACKTEST database following [Backtesting](BACKTESTING.md). Host scheduling, downtime and starting inventory are not recreated automatically. Report no-signal/no-fill/unfinished outcomes honestly; a synthetic success is not evidence that this overnight market offered an eligible trade.

## Position remains quarantined after close

Run the preview described in [Settlement recovery](SETTLEMENT_RECOVERY.md). Inspect `metadata_quarantine`, `settlement_evidence` and `settlement_blocked` records. Missing or unsupported final metadata is not a loss or a zero payout: accounting remains unresolved. Never edit checkpoint flags or delete the writer lease to force completion.

## Observed exit liquidity and submission diagnostics

Use **Trade history → Submission rejections** in the same mode and run as the
collector. Each expected submission refusal has a readable message, stable reason
code and expandable details (e.g. actual versus required edge, data age, or the
specific exhausted budget). Old generic records remain unchanged. No retained
opportunity is needed to view a compact-mode rejection. Rejected signal filters
are evaluations, not submitted orders; unfilled cancellations remain order records.

An unchanged bid does not refill itself. Fresh continuous observations of reduced
or disappeared depth allow later visible replenishment to be used conservatively.
First snapshots after gaps/restarts do not erase old consumed depth. Exits still
need safe data, fees, a signal, latency and available volume. See
[paper matching and diagnostic details](PAPER_TRADING.md#observed-exit-liquidity-and-submission-diagnostics).
