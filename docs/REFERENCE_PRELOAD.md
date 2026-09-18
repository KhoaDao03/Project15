# Official BRTI history preload

Collection now restores up to one hour of
validated official one-second BRTI samples before opening live subscriptions.
This official-reference preload is separate from the subsequent
[Bleep exchange seed and clamp](PROBABILITY_MODEL.md).

## Startup and rolling replacement

1. Read `data/reference-history.json`, a small atomically replaced reference cache.
2. If no usable cache exists, stream recent recordings indexed by the paper ledger
   (up to the latest 20 recording entries in this data directory).
3. Validate index identity, finite positive prices, original source/receipt times,
   timestamp ordering and duplicates. Ignore 5 Hz display frames; reject synthetic,
   corrupt or conflicting recordings. Never invent missing prices.
4. Record a first `reference_history` event with the exact restored samples in the
   new source tape, then initialize reference history without evaluating trades.
5. Reconnect and require fresh live reference data, verified market metadata,
   sequenced books, normal collector recovery and new sustained-lead confirmations.

The cache is refreshed every 30 seconds and at shutdown. Original timestamps are
preserved; startup never relabels old prices as newly received. A crash can leave
the cache behind the tape, in which case the existing gap checks may require more
live history. Invalid or expired caches fall back to recordings or normal cold warmup.

Every new official reference sample enters the existing rolling one-hour window.
Samples older than one hour are removed. Project15 indicators are recalculated from that
window, so preloaded samples gradually age out and are entirely replaced within
an hour of continued collection. Indicator smoothing does not keep a separate,
permanent state from preloaded candles.

## What this accelerates—and its limits

With sufficiently recent, complete history, the model can be ready soon after
fresh feeds reconnect, rather than waiting to collect 33 new candles. This does
not guarantee a trade or a fixed startup duration. The first fallback scan can
take longer than a small cache load.

The original quality checks remain in force. A reference gap exceeding two
seconds in the recent quality window blocks entry. Completed minute candles still
require at least 58 samples, no internal gap over two seconds, and a contiguous
sequence ending at the latest completed minute. A shutdown gap can therefore
make the official-history-only Bleep fallback unavailable until 33 usable candles
exist again. The active exchange-seeded Bleep component uses its separate rolling
candle buffer and avoids this specific 33-minute delay; Project15 quality gates
still apply.
Loading history does not recover observations that were never recorded.

The startup audit record and collector health expose `reference_preload`, including
loaded sample count, source, newest age, maximum gap and rejected-file reasons.
`LOADED` means history was restored; it does not mean all entry checks passed.

## Replay and validation

The embedded preload event is accepted only as the engine's first event. It
cannot trigger entries, exits or settlement, restore order-book health, or count
as a new reference confirmation. Offline replay uses the recorded samples and
does not depend on the current cache. All subsequent live and replay events use
the same engine.

Regression tests cover cache expiry and rolling replacement, future/stale times,
duplicates, corrupt/synthetic/conflicting inputs, replay without a cache, fresh
confirmation requirements, collector startup and shutdown, and unchanged entry
and processing behavior. Deployment evidence is under
`data/runtime/reference-preload-v1/`.
