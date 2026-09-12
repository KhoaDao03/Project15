# Persistent market-pause handling

Scope: Settlement Edge PAPER/BACKTEST only. This corrects a venue-deactivation
restriction being lost when a later quote made the book valid again. The pause change
did not alter pricing/sizing; subsequent arithmetic corrections are documented
in [exact price and sizing](PRICE_SIZING.md).

## Policy

Quote freshness is not permission to trade. A `deactivated` lifecycle event, or
parser-valid REST metadata with `status=inactive`, creates a per-market restriction
in the executor's `venue_pauses` checkpoint. It cancels the unfilled entry remainder
but preserves filled inventory, fees, risk reservations and entry-attempt history.
Both new buys and pre-expiry sells remain blocked. An unrelated market is not
paused, and this does not change the separate risk kill-switch policy.

An `activated` event is a reactivation hint, not permission to reuse cached market
terms or quotes. It cancels resting remainders and requires confirmation as well.
The next qualifying metadata refresh must meet all of these conditions:

- It came through the collector's REST path (`source=kalshi_rest`).
- Its recorded `request_started_at` is strictly later than the latest pause or
  activation notification and no later than the response's receipt time.
- The clock check passes; the normal parser validates identity and settlement
  terms; status is `active`; and the market is within its trading interval.
- Its contract hash matches the original pinned contract, with no metadata
  quarantine, generic HALTED/ERROR state or pending/closed settlement state.

The collector timestamps the request **before** awaiting discovery. This prevents
an active response already in flight when the pause arrived from reopening the
market. A later validated active REST response can recover a missed activation
notification after reconnect/downtime; an activation notification is not mandatory.
After confirmation the old book is invalidated. A newly received valid snapshot
and all existing receipt/source freshness, reference, fee, clock, latency, signal
and risk checks are still required before execution. No fill is invented for the
paused interval. Cancelling an old entry does not grant a new entry attempt.

`closed`/`determined` restrictions cannot be cleared by an activation hint or a
conflicting active response. A genuinely changed close/settlement specification
continues through the separate [metadata recovery](SETTLEMENT_RECOVERY.md) process.
A price-grid change notification also requires a new valid metadata observation
and fresh snapshot, rather than permitting stale grid use.

This intentionally conservative confirmation policy can delay resumption until
the next metadata refresh (normally scheduled every 15 seconds, plus request and
processing time). It does not weaken a venue pause to increase trade frequency.

## Restart and accounting

`venue_pauses` is additive checkpoint state. New pause/resume audit rows and the
checkpoint commit atomically with any remainder cancellation. A failed write
rolls back and propagates; it is not reported as a successful pause or resume.
Restarts restore the restriction, start with unhealthy feeds/invalid books, and
require the same confirmation process. Old checkpoints without this field remain
readable; missing historical pause information is not invented. Old tapes without
post-pause REST request provenance may remain blocked; do not edit their events
or checkpoint to force a result. Capture/replay trusted inputs with the new
collector. The provenance marker is not a venue cryptographic signature.

Official settlement is separate from pre-expiry selling and still works while
paused, subject to the existing evidence and metadata-recovery checks. Successful
terminal accounting clears the venue restriction and writes the result/P&L once.
A market resume never clears a risk kill switch or metadata quarantine.

## Diagnostics

The collector status exposes `venue_pauses`, and the positions/risk panel shows
blocked markets. Current evaluations use `VENUE_PAUSED`; direct submission
refusals use the same stable code with pause details. Compact-mode audit records
`market_pause` and `market_resume` are retained even without a filled opportunity.
They can be read through the existing records API and market replay timeline.
No new endpoint, settings toggle, manual unpause command or schema table is added.

Do not clear a lease, delete history, change a run ID or change risk settings to
bypass this restriction. Inspect the original run's metadata/status and the
recorded request/notification times. Entries may still fail their ordinary
filters after a legitimate resumption.

## Verification boundaries

Regression tests cover the original quote-after-deactivation failure, both modes
and sides, cancellation with held inventory, snapshot/reconnect/restart, stale or
in-flight responses, activation hints, verified resumption, ordinary stops after
resumption, quarantine isolation, atomic rollback, duplicate settlement, risk
preservation and readback from reopened SQLite/FastAPI handlers. Inputs/model
outputs in these targeted tests are synthetic/controlled; the normal CI also runs
the unchanged full-model synthetic fixture. See the PR's actual executed results.
These checks are not an authenticated live-feed session or profitability evidence.

Venue reference: [Kalshi market lifecycle](https://docs.kalshi.com/getting_started/market_lifecycle).
Kalshi documents `inactive` as a temporary trading pause, `activated` as a separate
reactivation event, and cancellation of resting orders on reactivation.
