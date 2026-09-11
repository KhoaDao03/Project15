# Paper exit execution: displayed-depth-v2

This execution update does not change entry rules, sizing, probability/stop thresholds,
or the configured latency. Historical fills and performance records remain immutable.
New sell fills identify their execution model as `displayed-depth-v2`.

## Matching and pending intent

An exit becomes eligible 250ms after its decision under the current active settings.
Both processing time and the book observation's receive time must reach eligibility.
An old queued snapshot cannot satisfy latency just because processing it took longer.
The engine's health, source/receive freshness, market status and quarantine gates remain.

Match actual supported bid levels, highest first, limited by displayed unconsumed depth
and position quantity. Profit-taking sells must meet the configured target. Other exits
consume available supported bids, so thin depth can produce a worse average sale price.
Each matching event is an IOC child execution. Its fills share one fee accumulator;
later matching events start new accumulators. There is no additional automatic haircut
in primary fills. The slippage setting still governs existing entry behavior.

Pending exits are conditional. If the triggering reason clears, cancel the intent and
clear its eligibility timer. A different triggering reason also starts a new intent and
latency period. Completed partial fills are final; cancellation only affects the unsold
remainder. Unchanged displayed liquidity remains consumed across cancellation and
checkpoint recovery. A missing probability does not prove a probability trigger has
recovered: retain its intent, but do not execute a probability exit without a usable
model. Price-driven risk reduction remains available under the existing engine gates.

## Separate stress reporting

Each primary sell fill has a corresponding `exit_stress` audit record. It applies the
configured haircut and downward tick rounding to the same observed price and quantity.
This is a same-opportunity diagnostic, not a separate trading path or strategy backtest.
Stress records never update inventory, realized P&L, trade counts or accuracy.

Records contain the shared `exit_execution_id`, displayed price, primary fee/proceeds,
haircut, stressed price, sell limit, and stressed fee/proceeds if fillable. A haircut
below the profit limit is `NOT_FILLABLE_AT_LIMIT`, with no hypothetical fee/proceeds.
A haircut producing no executable tick is `NO_VALID_TICK`; primary execution still works.

Primary fills include `book_received_at`, `book_source_at`, `exit_eligible_at` and
`execution_model`. Trigger recovery/change is recorded as `exit_cancelled` with the
previous reason and remaining quantity. Existing records need no migration.

Read stress records through the existing records API, for example:
`/api/records?kind=exit_stress&mode=PAPER&run_id=settlement-convergence-v7`.
These records are separate from the dashboard's primary performance figures.

## Validation

Regression tests cover delayed pre-eligibility snapshots, the eligibility boundary,
YES/NO depth matching, a 99-cent bid filling a 99-cent target, stress-limit failures,
unsupported stress ticks, trigger changes, missing models, partial fills and checkpoint
recovery without reusing depth. Existing price/quantity and atomic rollback checks remain.

A fixed-purchase replay of the original nine v7 trades produced -$0.0927 net with this
execution model versus -$0.4874 with the old model on the same receipt clock. The largest
stop was -$2.3220 versus -$2.2695: correctness does not guarantee better individual fills.
The recorded collector result was -$0.5197 overall; processing-clock differences mean
neither replay is an exact reconstruction of live collector scheduling. No alternative
stop confirmations or probability thresholds were added. Local evidence:
`data/runtime/execution-v2-replay.json` and `data/runtime/replay_execution_v2.py`.
