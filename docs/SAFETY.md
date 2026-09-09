# Safety and recovery

## Current boundary

This milestone is one Settlement Edge paper strategy. `Settings.guard()` rejects LIVE or live-enabled configuration and `LiveTrader.submit()` raises. Read-only account reconciliation and a payload builder are not working real-money order submission. Keep `TRADING_MODE=PAPER` and `ENABLE_LIVE_TRADING=false`.

The dashboard binds to localhost and has no multi-user authentication boundary. Do not expose it through a public bind, tunnel or reverse proxy. Its Settings endpoint validates next-session JSON; saving does not alter a running engine. Origin checks are not a substitute for remote authentication. The UI history/mode selector does not change the running portfolio or the target of shutdown.

Keep `.env`, the RSA key and backups private. Do not commit, print or upload credentials. A shared diagnostic needs redacted logs/tapes and matching configuration, not the signing key.

## Operating the kill switch

With the same `DATA_DIR` and database environment as the running writer:

```bash
uv run --locked btc15 halt
```

This writes `DATA_DIR/HALT`. The collector observes it during processing, blocks entries and cancels resting remainders. It does not guarantee liquidation; existing positions remain subject to available management/settlement inputs. The halt is latched in the process and recorded checkpoint. Removing the file alone does not clear a halted resumed portfolio. Do not edit checkpoint risk flags to bypass it.

The CLI currently loads/validates strategy configuration before dispatching `halt`. A malformed saved config can therefore prevent the command from setting the file. Verify that the file was actually written; a manual local creation of the exact `DATA_DIR/HALT` file is the underlying switch. Do not confuse a CLI error with a successfully halted bot. Resolve the incident and exposure before any restart/new run.

For a planned stop, prefer **Shut down safely** or the managed service's SIGINT/SIGTERM path. Settings → Enable entries is for a new session, not an immediate emergency stop. A tab closing, missing network or PC sleep is not a confirmed coordinated shutdown.

## Existing-position management after an entry stop

An entry halt or inactive entry policy does not by itself suppress ordinary exits
from filled inventory. Price-based stops/take-profit can operate during model
warmup or unavailability, but only with healthy current reference/book data,
verified fees, clock/processing checks, an active exchange and a tradable contract.
Probability-based exits still require a usable model. A stop is not guaranteed,
no position is force-liquidated, and the risk halt remains latched after selling.
Contract-state quarantine (`HALTED`/`ERROR`) is not bypassed. See the explicit
[permission matrix and recovery limits](POSITION_MANAGEMENT.md).

## Back up before updating

First obtain a clean stop/acknowledgement where possible. Preserve the exact code revision, frozen strategy config, database, raw tapes and any operational logs. Keep credentials protected separately. On SQLite, use a consistent database backup rather than copying only an active `.db` while its WAL is changing. One example after stopping all writers is:

```bash
uv run --locked python -c "from pathlib import Path; import sqlite3; source=Path('data/btc15.db').resolve(); target=Path('data/backups/btc15-before-update.db'); target.parent.mkdir(parents=True, exist_ok=True); target.touch(exist_ok=False); src=sqlite3.connect(source.as_uri()+'?mode=ro', uri=True); dst=sqlite3.connect(target); src.backup(dst); dst.close(); src.close()"
```

Adjust both paths to the actual database; the example refuses an existing destination. Validate the backup and copy the matching raw/frozen-config files before a change. This does not back up a remote PostgreSQL database; use its consistent backup procedure separately. No guide instructs deletion of a live database, WAL, claims or checkpoints as routine setup.

## Crash recovery

1. Confirm the previous process is actually stopped. Preserve originals, logs, raw tapes, database and checkpoint/configuration before intervention.
2. Inspect the exact collector lease and run's orders, fills, results and positions. Never clear ownership while a writer could still be alive. A zero completed-trade total does not rule out open inventory or pending entries.
3. Audit/replay copies in a separate BACKTEST database to understand the sequence. Damaged JSONL/gzip requires an explicitly documented repair/exclusion in a copy; do not silently trim originals or invent missing data.
4. Verify a compatible atomic checkpoint and its original config/source identity. Missing checkpoints or unsupported legacy groups need forensic/legacy recovery, not guessed queue/fee/portfolio state.
5. Only after proving the crashed owner cannot run, an operator may release that exact stale lease transactionally. No generic delete-all SQL or automatic expiry is supplied. Resume the compatible run/configuration; resting remainders are cancelled, filled exposure remains and feeds must regain health. See [Paper trading](PAPER_TRADING.md).

For retired portfolios, [SINGLE_STRATEGY.md](SINGLE_STRATEGY.md) identifies the pinned compatible revision. Never rewrite a group manifest to discard children, erase archived fills, or start a new database/name to reset risk usage.

## Updates and intentional changes

After a clean stop and backup, inspect local modifications, fetch the intended branch, and fast-forward only when appropriate. Do not use `reset --hard`, forced checkout or blanket cleanup on a running installation. A code change takes effect only after process restart. Existing runs require their original configuration; not every source change is guaranteed checkpoint compatible. Compare recorded source hashes and consult the change's recovery notes.

## Fail-closed behavior and remaining limits

Unexpected data errors invalidate books and cancel entries. Recorder/database errors stop operation rather than continuing without evidence. Queue overflow stops capture; clock/sequence/freshness problems must be diagnosed, not bypassed. The 5 Hz UI reference is separate from standard settlement samples and execution-time freshness.

Host-specific clock repairs in [COLLECTION_RECOVERY.md](COLLECTION_RECOVERY.md) are historical; do not disable time services on another host by copying those commands. Use one properly managed clock source for your actual environment and verify new captures. Hardware/host endurance, authentic feed validation, backup restoration and PostgreSQL remain separate acceptance work. No safety guard or passing synthetic test guarantees a profit or a stop fill.

## Metadata quarantine and settlement

A quarantined held contract stays tracked with reserved exposure. Only verified finalized evidence can complete its accounting. Matching original terms may recover automatically; changed terms require explicit evidence-bound review. The recovery command takes the existing writer lease and never reopens entries, changes a result by user input or resets a kill switch. See [Settlement recovery](SETTLEMENT_RECOVERY.md).
