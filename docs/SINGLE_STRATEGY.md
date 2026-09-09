# Settlement Edge-only scope and recovery

## Implementation review (before changes)

Approved scope: remove Conservative Confirmed Momentum and Volatility-Regime
Momentum from active execution and the normal dashboard, not their historical
records. Baseline: `b4f291105b2f1bbb8f025b82f922abe9b22084f4`, including the
fractional passive-fill fix and locked validation workflow.

The dependency review traced the CLI, dashboard, runner, Engine, PaperExecutor,
registry/identity helpers, analytics, replay, benchmarks and shared safety tests.
ModelGroup restored children from an immutable manifest. Deleting its algorithms
without a resume guard could strand old positions. The dashboard also summed all
strategies: hiding cards alone would leave misleading headline results.

High-priority preservation: no threshold changes, no data reset, no schema migration,
no new dependency, no real-money orders, and no silent conversion of group checkpoints.
Medium-priority changes: retire registry/dispatch, scope read APIs before counting,
and keep the active configuration visible even when entries are disabled.

## Current architecture

One collector -> one Settlement Edge Engine -> shared PaperExecutor -> SQLite or
PostgreSQL ledger/checkpoints + compact raw input tape -> dashboard. The former
`models.py` is now only identity, historical classification and recovery guards;
it never imports or instantiates a retired algorithm. The original and moderate
configurations, probability model, fees and limits are unchanged.

`btc15 dashboard` starts/resumes one named PAPER run (default `dashboard-paper`).
`--observe-only` records/evaluates without simulated orders. `--no-collect` displays
an external writer. `btc15 paper`, `paper-service`, `collect`, `backtest`,
`walk-forward`, `audit`, `export`, `analytics` and safe shutdown remain available.
The `models`, `model-paper`, `model-backtest`, and `model-comparison` commands are
removed deliberately. They fail argument parsing rather than silently doing less.

## Existing installations: retire before switching

This commit does not inspect, stop, migrate, or modify the laptop's running bot.
Back up its database (including a consistent SQLite backup, not an arbitrary copy
of an active WAL file), raw tapes, frozen settings and source revision first.
Use the compatible baseline above to stop new momentum entries, cancel unfilled
orders, and let any filled inventory settle. Verify orders, fills, results and
checkpoints; a zero completed-trade counter is not proof of zero exposure.

The new runner rejects pending/open archived exposure and refuses to resume a
manifest containing retired children, even if those models were disabled later.
The error identifies the run and compatible recovery revision. Do not delete
leases, manifests, orders or checkpoints to bypass it. No automatic liquidation,
background cleanup, portfolio migration, or historical rewrite is implemented.

After legacy exposure is resolved and the writer has stopped cleanly, start a
**new** Settlement Edge run ID with the deliberately chosen frozen configuration.
The previous multi-strategy root is not reinterpreted as a single-strategy run.
Single-strategy checkpoints and old group manifests with no children can resume
under their original run ID/configuration. Existing control daily-risk history
still carries forward; a new run is not a risk-budget reset.

The preset is not changed by this refactor. For the moderate preset, an example is:

```bash
uv run btc15 --config config/settlement-edge-paper-moderate.json dashboard --run-id settlement-paper --port 8001
```

Repeat that exact configuration/run selection to resume. For an original-control
run, keep its original frozen config file instead. Saved UI settings apply only
to new sessions; they do not modify a running strategy/checkpoint.

## History and UI/API changes

The default UI shows one strategy card and Settings, not a strategy library.
Headline results cover its selected configuration (or explicit run), excluding
retired strategies. Trade history distinguishes orders/cancellations, fills,
completed trades, and entry evidence. Compact paper mode stores first-fill evidence,
not every rejected evaluation; empty evidence history does not imply no evaluation.

The History selector exposes **Archived strategies (read-only)** separately.
`/api/runs`, `/api/records`, `/api/trades`, and `/api/analytics` accept
`scope=settlement` (default) or `scope=archive`. Filtering occurs before counts,
groups and pagination. `/api/replay/{id}` still reads retained records by ID,
including archived evidence. No historical row is deleted. Legacy control records
without explicit identity remain controls; child run IDs without identity are
classified as archived. Scope never activates an algorithm or changes execution.

`/api/strategies` remains as a compatibility route returning exactly one Settlement
Edge card; it is not an activation/registration endpoint. `run_id` selects a
historical control configuration. `/api/strategy` retains validated new-session
settings writes. Security/origin checks and the live-trading block are unchanged.

```bash
uv run btc15 analytics --mode PAPER --run RETIRED_RUN_ID --archive
```

All analytics remain limited to retained evidence. Full counterfactual or rejection
analysis needs the complete raw tape and original configuration; actual historical
fills are not replaced by a new replay. Backtest totals are not a live portfolio.
Old momentum guides/benchmarks are marked historical, not executable instructions.

## Verification plan

Preserve the original model/config/rules and preset file hashes. Run the same
synthetic tape in the same locked environment before/after: compare probabilities,
features, reasons, decisions, orders, fills, fees, transitions, results and remaining
risk for original/moderate configurations. Run the full offline suite including
fractional-fill regression tests, new startup/resume/archive guards, lifecycle,
rollback, compact recording and API tests. Check lint, changed-file formatting,
Python/JS syntax and package builds. `scripts/verify_settlement_behavior.py` records
or compares that synthetic behavior without network or production data access.

Authentic overnight tapes, host performance and active laptop positions are not
available to this implementation session. A synthetic parity result is not proof
of market eligibility, live execution quality, or profitability. Unrelated audit
issues are intentionally not bundled into this scope change.
