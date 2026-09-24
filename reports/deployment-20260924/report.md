# Deployment — 2026-09-24

Authorized ETH multiplier 0.95 and all pending changes deployed at 04:22 UTC.
All seven collectors, the executor and both dashboards restarted successfully;
all ten were active with zero automatic restarts during verification.

## Applied changes

- ETH sigma multiplier changed from 1.10 to 0.95; verified in a fresh live probability calculation.
- ETH now uses `ETH-atr095-20260924T042224Z-signals`. The previous run is retained, with original reference-history timing copied into the new run. Frozen policy hashes remain unchanged because the multiplier is a code constant; the new run and recording manifests distinguish the model change without rewriting old checkpoints.
- Seven collectors now use sampled/schema-3 recording; seven executor recorders remain full/schema-2. Sampled books are observations for estimated backtests, not exact execution replay.
- Journal retry diagnostics, interval-scoped coverage, archive retention and offline coverage rebuilding are deployed. Closed segments have begun moving into `research-logs-archive`.
- Pending BTC backlog-entry policy and public dashboard probability display changes are deployed.
- Existing switches, quantities and asset risk settings were preserved: BTC/ETH/SOL/XRP enabled at 10 contracts; GOLD/SILVER/WTI disabled at 10, with their collectors running.

## Verification

- Full suite: **1,882 passed, 3 skipped**, including the browser test. Missing Chromium runtime libraries were installed to run it.
- Focused Ruff checks and whitespace checks passed.
- All collectors connected with fresh status; ETH live calculation reported `sigma_multiplier=0.95`.
- All 14 new recorders reported zero accidental drops, no current diagnostics and the intended recording mode. A transient SQLite lock retried successfully without producing a false capture gap.
- Private fleet API, public API and dashboard JavaScript returned HTTP 200. Served JavaScript matches the working tree; deployed source hashes are recorded.
- All collectors reached healthy status after startup. Subsequent samples showed intermittent `WAITING_FOR_FRESH_METADATA` recovery gates (BTC/SOL/XRP); final verification records the actual states rather than assuming continuous entry eligibility. This deployment does not disable those metadata gates.

## Evidence and limits

See [test output](tests.txt), [cutover](cutover.json), [verification](verification.json), and [source hashes](deployed-hashes.json).
Host backups: `/root/project15-backups/eth095-20260924T041518Z` (stopped databases, configurations, service definitions and deployed source).
Historical missing data cannot be reconstructed by these fixes. The backtester on the other machine still needs the [reader changes](../research-repair-20260924/BACKTEST_READER_HANDOFF.md).
Archive retention is local, not an off-host backup; the free-disk reserve still applies.
