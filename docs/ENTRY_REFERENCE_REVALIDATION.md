# Entry revalidation against received reference updates

The September 10 investigation found that the 06:45 contract's adverse BRTI tick
arrived about 68 ms before its simulated buy, while the worker was processing an
earlier book event. Both reference age and processing lag passed the existing
freshness limits. A one-second probability cache could also outlive a newly
processed reference update.

## Corrected behavior

The live receiver marks the latest received official 1 Hz reference event by ID
and receipt time. It does not put its price into the model or reorder events.
The ordered worker continues to persist and validate inputs before applying them.

While a marked reference event has not been processed:

- New order submission is rejected with `REFERENCE_UPDATE_PENDING`, without a
  risk reservation or submitted-order attempt.
- Pending IOC matching waits rather than consuming an old book immediately.
- Passive matching cannot buy from an earlier trade event while that update is
  outstanding. Skipped trade events are not replayed later to manufacture fills.
- A final buy-fill guard covers a reference arriving during matching.

Waiting records `entry_revalidation_wait` once per order/reference event. Pending
orders remain subject to ordinary timeout, entry-window, health and cancellation
rules; waiting does not extend their lifetime or allocate another retry.

Processing a new reference tick refreshes model features/probabilities even inside
the normal one-second calculation interval. Quote-only updates still reuse the
cache when the reference has not changed. After the worker catches up, the pending
order must pass its normal revalidation. An adverse update cancels the remainder;
a qualifying signal may still fill at an eligible event.

This gate does not delay held-position exits, change strategy thresholds, add a
minimum holding period, or anticipate a reference that has not arrived. The first
investigated trade filled before the adverse reference arrived and is not prevented
by this receipt check alone. The separately disabled hold-value exit remains a
strategy choice.

## Persistence and replay

Receipt markers are transient process state, not portfolio checkpoint fields.
Configuration hashes remain unchanged; existing portfolios resume normally and
rebuild fresh feed state. New-reference cache invalidation also applies in offline
replay. Ordinary receipt-ordered replay has no live worker backlog, so it cannot
reproduce a receipt-versus-processing delay merely from order of input events.
The regression tests explicitly simulate that delay; live wait audit records
identify when this operational safeguard acted.

Tests cover queued-reference deferral, adverse cancellation, healthy continuation,
submission without budget consumption, subsecond model refresh, and the receiver/
worker handoff. Recorded inputs and the original model reconstruction are retained
locally in `data/runtime/immediate-exit-inputs.json` and
`data/runtime/immediate-exit-model-trace.json`.

## Active v6 committed entries

The user selected `revalidate_entry_signal=false`. New submissions still use
current entry rules and wait for received reference updates to be processed.
Already-submitted orders keep their original side and limit even if updated
probability, lead, price-policy or net-value checks fail. Freshness/health, market
tradability, timeout and entry cutoff still govern execution. The original price
cap is enforced and any IOC remainder is cancelled. Existing exit rules can act
after purchase. Historical configurations default to signal revalidation.
