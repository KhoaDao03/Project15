# Reader changes for the backtester on the other machine

This is an interface guide for staged recorder changes, not a patch to your
backtester. Keep strict replay and estimated execution results in separate tables.
The new files will exist only after deployment with sampled capture enabled.

## Recognize the mode

| Manifest | Reader behavior |
| --- | --- |
| `schema_version=2`, `capture_mode=full` (or absent) | Full-capture input stream; still verify gaps and dependencies |
| `schema_version=3`, `capture_mode=sampled`, `exact_replay=false` | Require an explicit estimated-execution option |
| `schema_version=1` | Older sampled format; use its existing dedicated reader |

Schema 3 intentionally omits `input` records for order-book deltas and all
`input_processed` records. A decision can therefore refer to an omitted input ID.
This is not an unexpected loss. All emitted records still have contiguous
`capture_seq` unless an actual loss is explicitly recorded. `sampled_out` counts
intentional omissions; `dropped` counts unexpected losses. Do not turn sampled
omissions into artificial recording gaps or treat `capture_complete=true` as
permission for exact execution replay.

Reference/trade inputs, accepted reference samples, actual model calculations and
decisions retain their existing formats. Original message strings are in
`input.body.payload`. Preserve exact reference values and causal history/seed
inputs. Do not interpolate reference prices or use later samples to repair earlier
ATR inputs. The reference ATR uses 14 true ranges from 15 contiguous candles,
including the causal live minute, rather than 15 completed candles or Wilder ATR.

## Use the observed book snapshots

Sampled `book` records contain:

- `body.market`, `body.valid`, `body.fresh`, `body.source`, `body.received`.
- `body.yes_levels` and `body.no_levels`: complete arrays of `[price, quantity]`
  strings, representing bids for each outcome. Level order is unspecified.
- Existing best-price/quantity and top-five summary fields for compatibility.
- `body.capture_mode="sampled"` and the usual envelope sequence and clocks.

For YES asks, convert NO bids with `yes_ask = 1 - no_bid` and retain the quantity;
reverse the conversion for NO asks. Parse prices and quantities as decimals.
Books are observed once per second plus threshold crossings, not on every update.

Process records in session capture order. At a simulated action time, select only
a snapshot whose `captured_at` is no later than that time (and whose receipt/source
clocks also satisfy your freshness rules). `recorded_at` is disk-writer time, not
the exchange observation time. Reject invalid, stale or too-old books rather than
carrying them indefinitely. Make the maximum book age explicit in results.
Never use a later snapshot as the earlier price, even if its source time is older.

A fill using a sampled book is an estimate: one-second observations do not establish
what price/liquidity existed after 250 ms, queue priority, or actual execution.
Report these assumptions and keep actual exchange fills distinct.

## Filter intervals, not whole sessions

Combine the complete closed segments across rotations: a ten-minute segment is
not a fifteen-minute market. Include both active and archived log trees and their
manifest/source files. Copy the archive first, then newer active closed segments,
into one destination with the supplied additive archive tool. Exclude `.part` files.

Run the second copy with `--rebuild-coverage`. In `archive-report.json`, each session's
`rebuilt_coverage` has per-market `incomplete_reasons`, `recording_gap_indexes`, and a
root `recording_gap_intervals` array. Each index points into that root array.
Known gaps overlapping a market or its preceding hour of reference dependencies
produce `RECORDING_GAP_IN_DEPENDENCIES`; unlocated losses remain
`RECORDING_INCOMPLETE`. Rebuild errors remain `COVERAGE_REBUILD_REQUIRED`.

Do not reject every market solely because session `capture_complete` is false.
Do not simply ignore that flag either: inspect missing prefixes/tails, source
context, book sequence/reconnect issues, causal reference/model readiness, official
settlement evidence and (for actual-bot replay) execution checkpoints and events.
A full interval can span segments. Combining partial sessions requires separately
re-establishing dependencies; it is not automatically certified by this report.
Rebuilds preserve old generic gap markers because their missing event types are
unknown. Recorded historical losses have not been declared harmless retroactively.

For estimated mode, a valid later observed book can supply a new starting state,
but it does not repair the earlier unknown interval. Require valid reference/model
inputs for each accepted decision, and report rejected intervals and their reasons.

## Report enough to audit the result

For each asset, include candidate intervals, accepted strict intervals, accepted
estimated intervals, exclusions by reason, trades, and the book-age/latency/fee
assumptions. Zero accepted intervals must be reported as insufficient usable data,
not as a measured zero-return strategy or evidence for an optimal multiplier.
