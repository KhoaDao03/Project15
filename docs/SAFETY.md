# Safety and recovery

## Current boundary

Paper execution and real orders are separate. Keep `TRADING_MODE=PAPER` and
`ENABLE_LIVE_TRADING=false`; real orders use the independently controlled
[live executor](LIVE_AUTOMATION.md). Its saved enablement survives ordinary restarts.

Keep the private dashboard on localhost and use an SSH tunnel. Publish only the
separate public viewer on port 8001 through the [cloud setup](CLOUD.md). Origin
checks are not remote authentication. Protect keys, `.env`, journals and backups;
share redacted evidence, never credentials.

## Operating the kill switch

For a paper collector, use its actual `DATA_DIR`, database and configuration:

```bash
uv run --locked btc15 halt
```

This writes `DATA_DIR/HALT`, blocks paper entries and cancels pending remainders.
It does not liquidate positions. The halt is latched in the process/checkpoint;
removing the file does not clear a resumed halt. A malformed saved configuration
can prevent the command from running: verify that the exact HALT file was written.
Do not treat this paper command as confirmation that live automation stopped.

For live controls, disabling new buys keeps exits active. Manual takeover pauses
market management and requests cancellation of the bot's resting offer. Planned
shutdown must be positively acknowledged; managed positions or unresolved orders
can block it. Closing a browser or SSH connection does not stop the bot.

## Existing-position management after an entry stop

An entry block does not itself remove filled inventory or disable ordinary paper
exits. Freshness, market validity, fees, quarantine and execution checks still
apply; probability exits need a usable model. Already committed live stops have
separate outage/retry behavior. See [paper position management](POSITION_MANAGEMENT.md)
and [live exits](LIVE_AUTOMATION.md#exits).

## Back up before updating

Preserve the exact source revision, frozen configurations, ledgers, raw inputs,
order journal and operational logs. Protect credentials separately. Use SQLite's
backup API or stop all writers before copying databases; copying a live `.db`
alone can omit its WAL. A consistent SQLite example, with paths adjusted to the
actual installation and a new destination:

```bash
uv run --locked python -c "from pathlib import Path; import sqlite3; source=Path('data/btc15.db').resolve(); target=Path('data/backups/btc15-before-update.db'); target.parent.mkdir(parents=True, exist_ok=True); target.touch(exist_ok=False); src=sqlite3.connect(source.as_uri()+'?mode=ro', uri=True); dst=sqlite3.connect(target); src.backup(dst); dst.close(); src.close()"
```

Validate backups and keep an off-host copy. PostgreSQL needs its own consistent
backup procedure. [VPS maintenance](VPS_MAINTENANCE.md) covers fleet-wide shutdown
and updates. Never delete journals, positions, claims or checkpoints to free space
or bypass startup checks.

## Crash recovery

1. Confirm the previous owner is stopped; preserve the original files and logs.
2. Inspect pending orders and positions, not just completed-trade totals.
3. Investigate copies in a separate replay database. Document damaged intervals;
   do not silently trim original tapes or invent missing inputs.
4. Resume only a compatible checkpoint with its original configuration. Release
   an exact stale lease only after proving its owner cannot still run.
5. Reconcile uncertain real orders before replacements. Paper resume cancels
   pending entry remainders, restores inventory and waits for healthy fresh inputs;
   it does not synthesize fills during downtime.

Use [legacy recovery](SINGLE_STRATEGY.md) for retired portfolios. Quarantined
contracts retain exposure until [verified settlement](SETTLEMENT_RECOVERY.md);
recovery never accepts a user-invented outcome or reopens entries.

## Updates and limits

Review local changes and the intended revision before updating. Do not use forced
checkout/reset/cleanup on a running installation or rewrite checkpoint hashes.
Configuration changes need a compatible migration. Documentation-only edits need
no restart. Logging v2 was deployed with authorization on 2026-09-20.

Required ledger/tape failures and optional research-recorder failures are different:
portable capture can drop records while trading continues, and reports those gaps.
A moving display or passing test does not prove feed continuity or complete evidence.
Historical clock repairs are host-specific. Liquidity, latency and outages can
prevent a desired exit price; no test establishes profitability.
