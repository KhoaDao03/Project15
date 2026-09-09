# Paper trading — Settlement Edge only

Paper operation uses authenticated market inputs but local simulated orders/fills. The CLI labels both paper execution and observation-only collection as PAPER; inspect the execution flag, not the label alone. Start with [Getting started](GETTING_STARTED.md) for credentials, a database and a frozen configuration.

## Dashboard-owned paper execution

```bash
uv run --locked btc15 --config data/runtime/settlement-original.json dashboard --run-id settlement-original --port 8000
```

This starts/resumes one Settlement Edge engine and the UI. Without `--run-id`, the default is `dashboard-paper`. Paper startup checks compatible history/checkpoints, writer ownership and a 10 GiB data-filesystem reserve. The process remains subject to every signal/freshness/risk gate. Neither `dashboard` nor `paper` guarantees a fill.

## Separate process and viewing dashboard

For a standalone fresh paper run:

```bash
uv run --locked btc15 --config data/runtime/settlement-original.json paper --seconds 1800
```

This creates a generated run ID and prints it when the command completes. It does not take `--run-id`. Find the run in the dashboard/history as well. Resume it explicitly, replacing `RUN_ID`:

```bash
uv run --locked btc15 --config data/runtime/settlement-original.json paper --resume RUN_ID
```

For stable named operation on Linux/WSL, including an optional bounded acceptance session:

```bash
uv run --locked btc15 --config data/runtime/settlement-original.json paper-service --run-id settlement-original --seconds 1800
```

Omit `--seconds` for continuous foreground service operation. Native Windows does not implement the POSIX signal-handler path used here; use the dashboard workflow or a Linux/WSL service. See [Managed paper](AUTONOMOUS_PAPER.md).

In another terminal, with the **same database and data directory**:

```bash
uv run --locked btc15 --config data/runtime/settlement-original.json dashboard --no-collect --port 8000
uv run --locked btc15 paper-health --run-id settlement-original
```

The health example applies to that named run; replace it for a generated ID. Do not start `dashboard` without `--no-collect` next to a writer. Changing the port does not allow a second writer on the same database. `paper-health` can exit 1 during warmup or degraded operation; its probe thresholds differ from strategy entry thresholds.

## Observation without orders

```bash
uv run --locked btc15 --config data/runtime/settlement-original.json collect --seconds 1800
```

Or run `dashboard --observe-only` instead of a paper writer. Observation keeps full evaluation history and JSONL/Parquet inputs. Paper keeps compact inputs and first-fill evidence. `--observe-only` and `--no-collect` are mutually exclusive. Do not run observation and paper collectors simultaneously against one database.

## Execution semantics

A qualifying evaluation is not a fill. Immediately before submission the executor rechecks time, fresh reference/book, market status, price grid, net EV, risk budget and the unique market-entry claim. Orders have a latency eligibility time; stale trades or trades published before eligibility cannot fill them.

Passive matching requires the opposing taker side and eligible volume at/through the limit. Same-side depth at/better than the proposed quote, multiplied by the configured queue multiplier (1.5 by default), models queue ahead. Newly modeled queues round up to the 0.01-contract increment using Decimal arithmetic. Residual volume from finer legacy queues rounds down; no fill volume is invented. See [the fractional-fill correction](PASSIVE_FILL_FIX.md). Quote touches and apparent queue cancellations alone do not fill the order.

Resting orders are revalidated on material reference/book/trade events and heartbeats. Default patience is 20 seconds. Cancellation does not reverse an earlier partial fill. There is one entry attempt per market/run; cancelled attempts still count against daily attempt and gross-exposure limits. Do not reset a run to evade those budgets. An explicitly configured aggressive order uses depth after latency and cancels its unfilled IOC remainder.

Exits require eligible observed liquidity and configured stop/TP/invalidation conditions, or a received official settlement result. Stops are not guaranteed. Open inventory can remain pending while a feed/result is missing. Settlement pays only remaining inventory; a prediction is never treated as an official payout.

## Evidence and checkpoints

In paper mode the full candidate snapshot is held in the pending order checkpoint. **Only the first simulated fill permanently saves its entry evidence**, atomically with the fill. Later partial fills reuse it. An unfilled cancellation retains the order/cancellation but discards its pending opportunity evidence. Skipped evaluations/status are replaceable live snapshots. See [Recording](TRADE_RECORDING.md).

Each execution action commits ledger records, duplicate claims, state transitions and checkpoint together; a failed write rolls back the in-memory and SQL state. Resume requires the original configuration/mode, cancels resting remainders, retains filled inventory and daily limits, and starts with unhealthy feeds. No fills are invented for downtime.

## Dashboard settings and new sessions

**Settings** saves `DATA_DIR/strategy.json` for future sessions. Explicit `--config` takes precedence, and a running engine is not reconfigured. Saving or disabling entries is not a running-process emergency stop. See [Strategy](STRATEGY.md) and [the persistent kill switch](SAFETY.md#operating-the-kill-switch).

## Dashboard shutdown

Use **Shut down safely** and confirm. It requests the collector sharing that dashboard's database to stop entries, cancel unfilled remainders, save positions/checkpoints and flush received inputs. It is not scoped by the selected historical run, mode or archive filter. The dashboard waits for acknowledgement; a failed confirmation is not permission to remove a lease or force a second writer.

Open positions are retained without forced liquidation. Repeat the same named start command/configuration after a clean stop. On Linux/WSL a managed service also handles SIGINT/SIGTERM. A disconnected browser or closed tab is not a stop command; the process runs on the host, which must remain awake.

## Existing multi-strategy portfolios

This runtime cannot execute retired algorithms or silently drop their child portfolios. Nonempty group manifests, archived child resumes and unresolved archived exposure are rejected with a compatible-revision recovery message. Follow [SINGLE_STRATEGY.md](SINGLE_STRATEGY.md) before switching an existing installation. No reset/migration/deletion is part of normal startup.

Aggregate depth, queue position, counterfactual impact, attainable latency, live feed endurance and strategy profitability remain empirical questions. Synthetic tests and a successful public discovery call do not close them.

## Observed exit liquidity and submission diagnostics

Exit matching tracks depth already consumed by this simulator separately from
real sell fills. On each fresh, validated, in-sequence book observation in a
continuous connection, outstanding consumed depth is capped at the quantity
still displayed at that price (zero when the level disappears). Reappearing or
increased depth can then be used. Unchanged snapshots do not replenish it.
Partial reductions are conservative: existing consumption is retained up to the
remaining displayed quantity. No hidden refill, queue identity or market impact
is inferred. Rounding remains downward to the 0.01-contract sell increment.

Observation occurs even when no exit signal is active or the model/reference is
unavailable; it never executes a sale. All actual exits still require the
existing safety, metadata, fee, latency and signal checks. Stale/future-dated or
invalid books do not replenish depth. The first snapshot after a disconnect,
sequence gap or restart retains old consumed depth rather than assuming a refill
occurred during the missing interval. Checkpoints preserve this consumed-depth
accounting; no historical fills are rewritten.

A candidate is not an order. Expected submission refusals now retain a stable
`reason` code, readable `message`, `details`, run/mode/config identity and the
linked evaluation ID. Engine-dispatched attempts also include the triggering
snapshot ID and decision/submission times. See **Trade history → Submission
rejections** in the matching mode/run, then expand Technical details. The same
records are available through `/api/records?kind=execution_rejection` and the
existing market replay timeline. No retained entry-evidence row is required to
view a rejection in compact paper mode.

Examples include `EXECUTION_DISABLED`, `PROCESSING_LAG`, `BOOK_RECEIVE_AGE`,
`REFERENCE_SOURCE_AGE`, `NET_EDGE_RECHECK`, `METADATA_QUARANTINED`,
`DAILY_ATTEMPT_LIMIT`, `DAILY_LOSS_LIMIT`, `OPEN_EXPOSURE_LIMIT`,
`DAILY_EXPOSURE_LIMIT`, `ORDER_ALREADY_ATTEMPTED`, and `ENTRY_ALREADY_CLAIMED`.
A moved-price rejection shows current/evaluated ask, conservative probability,
fee/slippage estimates, net edge and required edge. Risk sizing reports the
binding budgets using the same arithmetic as execution, not a second calculator.
The existing `RISK_LIMIT` evaluation also contains that explanation.

Rejected entry filters are still evaluations, not submission attempts; compact
paper mode does not archive all of them. Cancellation and zero-fill events are
still orders, not submission rejections. An unexpected database/programming
error still propagates and rolls back; it is not hidden as a harmless refusal.
The executor retains its `PaperOrder`-or-`None` interface. Old generic history
remains readable; it is not retroactively assigned a guessed cause. No strategy
thresholds, presets, risk budgets, fees, dependencies or real-trading permissions
are changed. Increased eligible simulated exit volume is not proof that an actual
venue order would fill, and the changes do not establish profitability.
