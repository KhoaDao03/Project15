# Official-reference history preload

Startup restores up to one hour of the selected asset's accepted official
reference samples. This is separate from [exchange indicator seeds](PROBABILITY_MODEL.md).

## Startup and rolling replacement

1. Read the atomically saved `reference-history.json` cache. If unusable, inspect
   up to 20 recent ledger-indexed recordings in that data directory.
2. With research recording enabled, merge its accepted-reference journal. Keep
   original source/receipt times; exclude future receipts and reject conflicts.
3. Validate index, positive finite prices, receipt freshness, ordering and duplicates.
   Ignore 5 Hz display frames and reject synthetic/corrupt inputs.
4. Record the exact restored samples as the first `reference_history` input, then
   initialize history without evaluating or trading.
5. Require fresh live references, verified metadata, sequenced books, healthy
   collector recovery and new same-side confirmations.

The cache refreshes every 30 seconds and at shutdown. The research writer journals
accepted samples individually, narrowing the crash gap without guaranteeing that
queued samples survive. Missing history remains missing. New observations replace
old ones in the rolling hour; preload does not create permanent indicator state.

## Warm-up and diagnostics

Complete recent history can avoid collecting 33 new indicator candles. It does not
guarantee readiness or a trade. Completed candles need at least 58 samples, no
internal gap over two seconds and a contiguous sequence. A recent quality-window
gap over two seconds still blocks entry. The rolling ATR needs 15 contiguous
candles; exchange seeds cannot substitute for that official-reference ATR.

`reference_preload` reports source, sample count, newest age, maximum gap and
rejections. `LOADED` means restored, not entry-ready. Model capture adds missing
sample ranges, candle readiness and explicit fallback reasons; see
[research logging](RESEARCH_LOGGING.md#reference-continuity-and-warm-up).

## Replay and validation

Only the first engine event can preload history. It cannot restore book health,
count as a new confirmation, trigger execution or manufacture settlement data.
Replay uses the embedded samples, not today's cache. Never fill an earlier gap
with a price received later.

Tests cover expiry, replacement, timestamps, corrupt/conflicting history, cold
warm-up and replay without a cache. Earlier deployment evidence is recorded under
`data/runtime/reference-preload-v1/`; it does not establish current deployment status.
