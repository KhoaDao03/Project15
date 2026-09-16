# Paper exit execution: displayed-depth-v2

Current active presets use [full-position execution](FULL_POSITION_EXECUTION.md):
all sell decisions remain committed and consume highest bids first until the
held quantity closes. In that mode take-profit prices are triggers rather than
minimum execution prices. The conditional/price-limited behavior below remains
the historical default when `full_position_execution=false`.


The original execution update preserved entry rules, sizing, probability/stop thresholds,
and the configured latency. Later opt-in exit rules are described below, with current
thresholds in [active paper settings](ACTIVE_PAPER_SETTINGS.md).
Historical fills and performance records remain immutable.
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

Hard-stop exits are committed once triggered. Recovery above the stop does not cancel
them or restart latency. Pending entry orders are cancelled immediately; the exit sells
remaining contracts into the highest available supported bids after the original
eligibility time. Partial fills and checkpoints retain the commitment. Freshness,
market-pause and liquidity checks still apply: this guarantees continued exit attempts
on eligible observations, not a minimum price or a fill when no liquidity exists.

Other ordinary pending exits are conditional. If the triggering reason clears, cancel the intent and
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

## Profit-only value exit (paper)

The active paper run disables `profit_value_exit_enabled`; the following describes
the retained optional rule. Before submission, require two distinct
reference seconds, no more than 1.5 seconds apart and no more than 1.5 seconds old.
A cleared condition resets confirmation. Repeated quotes do not count as new references.

Use raw selected-side probability (`p_yes`, or `1-p_yes` for NO). Find a supported sell
price floor where selling the entire remaining quantity, after quantity-aware taker fees:

- yields at least $0.20 total trade net profit, including earlier proceeds and all paid fees;
- exceeds raw probability times remaining quantity by at least $0.01 per remaining contract.

Require enough unconsumed supported depth at or above that floor to cover the remaining
position at decision time. These are conservative full-quantity estimates, not promises
of a fill. No slippage haircut is subtracted from primary economics or fills.

After the second confirmation, `PROFIT_VALUE` commits a simulated sell IOC with a frozen
floor and cancels any remaining buy order. Probability recovery does **not** cancel this
submitted exit. Match once against the first valid post-eligibility observation, at actual
supported bids no lower than the floor. Any remainder expires (`IOC_REMAINDER_EXPIRED`),
then gets a new evaluation; partial execution need not realize the full $0.20 threshold.
If no usable observation arrives within two seconds after eligibility, expire it without
backfilling (`IOC_DATA_TIMEOUT`) when monitoring resumes. This receipt-based paper model
is an approximation of exchange IOC execution, not a resting order or a live API call.

Existing hard stop, 99-cent target and configured adjusted-probability exits have priority when
choosing a new exit. A submitted value IOC completes/expires before a new safety intent
can be submitted; it cannot execute below its floor. Safety evaluation resumes immediately
on the same observation if inventory remains. Other conditional exits retain their
existing trigger-clear behavior described above. Checkpoints retain the committed floor
and eligibility. The separate stop-confirmation observer uses this same value-exit logic.

`exit_intent.decision.profit_value` records probability, floor, confirmation count and
thresholds. Fills/results carry reason `PROFIT_VALUE`; haircut stress remains separate.

## Standard-entry net-profit cashout (paper)

The active configuration disables `standard_cashout_enabled`. When enabled, only positions entered
through the standard entry path qualify, and strictly more than 120 seconds must
remain both when submitting and matching the exit. At exactly 120 seconds the rule
is unavailable. Late entries retain their other exits.

Require enough unconsumed supported bid depth for the entire remaining position at
a sell-price floor satisfying:

```text
prior sale proceeds + remaining sale proceeds - total purchase cost
  - all paid fees - estimated exit fees >= $0.10 * total contracts purchased
```

The calculation uses quantity-aware taker fees and balance rounding. For ten
contracts, the target is $1.00 total net profit. No probability comparison or
reference confirmations are required. A position bought near 90–95 cents cannot
reach this target after fees, even with a winning settlement.

The priority for new exits is hard stop, 99-cent take-profit, standard cashout,
probability exit, hold-value exit, and the older profit-value exit. The last two
remain disabled in the active configuration. A cashout records `STANDARD_CASHOUT`
and `exit_intent.decision.standard_cashout`, including its supported sell limit,
profit target, remaining time and IOC policy.

After the configured 250ms delay, the first fresh eligible quote supplies one IOC
matching event. Each fill must meet the committed supported price floor. Probability
changes do not cancel a submitted cashout, but a hard stop can override it. A
remainder expires after that observation and is evaluated again; partial execution
does not establish that the whole trade earned its target. If the remaining time
falls to 120 seconds or below, cancel the cashout. If no usable quote arrives within
two seconds after eligibility, expire without backfilling. Inventory then remains
subject to the other exits and settlement.

The sixty-second pause starts only after the position fully closes; see
[sequential trades](REENTRY.md). This cashout differs from the older profit-value
rule above: it targets a fixed net gain per purchased contract and does not require
sale value to exceed modeled settlement value.
