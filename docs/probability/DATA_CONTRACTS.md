# Probability data contracts

## ContractSpec v2 and reviewed family profile

`contract_profile.py` recognizes the reviewed KXBTC15M template. Targets and timestamps remain dynamic. The adapter checks primary/secondary wording, CF identity, comparison, timing, precision compatibility, payout, and hashes of both series-linked PDFs. The collector preserves PDF bytes and retrieval provenance every five minutes; absent, changed, or stale evidence blocks verification. Contract records retain administrative expiration, expected expiration, and actual settlement timestamps separately.

The supported normal observation schedule is `[T-60,T)` at 1 Hz. `floor_strike` supplies the authoritative target for the matched greater-or-equal template. Arithmetic averaging precedes nearest-cent rounding and comparison. The half-cent tie convention remains unresolved, so `verified` stays false and the primary estimate stays unavailable. See [SETTLEMENT_VERIFICATION.md](../../SETTLEMENT_VERIFICATION.md) for exact evidence and clearing conditions.

A free-form `--rules-review` file can no longer grant verification. Supplying it adds an explicit blocker; a newly established rule requires a versioned profile change, preserved authority, and tests. Synthetic specifications remain independent test fixtures.

## Source and clock discipline

Events have source time (nullable when the source provides none), transport receipt time, availability/ordered receipt time, processing time, kind, payload, sequential ID, previous hash and SHA-256 hash. All timestamps are UTC Unix seconds. A producer delayed by queue backpressure can have transport receipt earlier than availability: both are retained; normalized inputs become eligible only at availability. Receipt clock regression is rejected rather than reordered silently. Network clocks are injectable.

CF WebSocket messages are normalized only from `msg.data` value objects with index BRTI. The 1Hz channel is selected, not 5Hz. Prices retain all decimal digits. Settlement accumulation uses **exact schedule timestamps**, no flooring of subsecond timestamps; timestamp alignment that cannot be verified yields missing samples. Identical duplicates do not increase counts; conflicting values invalidate affected samples. Source-order arrival is permitted, but information must already be received. History recovery retains its later availability timestamp. Unrecognized history shapes remain raw and generate an explicit failure.

Both provider aggregates remain diagnostic, never replacement samples. `avg_60s_data` at T describes `[T-60,T)`; the final-minute helper describes `(T-60,T]`. Reconciliation checks their different sample schedules, counts, both boundaries, receipt/source causality, and eight-decimal serialization tolerance. It compares the reconstructed value with published `expiration_value`, independently of YES/NO. Partial windows and exact half-cent ties have no resolved settlement value.

Options preserve instrument/expiry/type/strike, native quote and settlement currency, bid/ask/mark/size, index/forward, IVs, and source/receipt times. IV normalization divides percent by 100. Missing IV is not invented. A source-expiry forward is used for smile moneyness, never as the short-horizon forward.

The binary book preserves Decimal price and size. Asks derive from opposite bids only. Empty sides stay null; crossed books and sequence gaps invalidate comparisons. Reconnect requires a new snapshot. Live collection intentionally uses REST snapshots every two seconds; the tested WebSocket book reducer is available but not used by the initial collector. Thus live book completeness does not depend on incremental recovery. Last trade is null in this initial collector; it is not a renamed midpoint.

## Recording, replay, and restart

SQLite uses WAL, FULL synchronous durability, and one exclusive advisory writer lock. `events` is logically append-only; no update/delete path exists in the application. Hash-chain verification detects mutation when replaying. `forecasts` is an append-only projection keyed by tick event/market/provider. Each forecast lists input event IDs plus its forecast tick, and carries model/config/rules versions. Raw response bodies exclude request headers/keys. No credentials are serialized.

Reopening requires exactly the same mode and configuration. Restore replays the event log and fills any missing forecast projection after an interrupted write. Events retain arrival order, no sorting using future source timestamps. Seeds and recorded timer events determine forecasts; no network calls occur during replay. New information cannot rewrite a historical forecast. Synthetic files cannot be opened as live recordings.

The dashboard makes read-only SQLite connections and serves only GET/HEAD. Network adapters whitelist public market reads and authenticated CF reads/subscriptions. They cannot sign arbitrary portfolio paths and expose no order function. Existing Project15 trading code is not imported or mounted. This is an application boundary; the rest of the repository still contains its original trading functionality.

## Forecast and evaluation

Forecast records include readiness/reasons, exact provenance, raw/complementary/null-calibrated probabilities, value/discount, model version/config hash/rules hash/input IDs, prices/times/counts/ages, terminal d2, effective/implied/realized volatility, integrated variance/drift, simulation interval and precision result, parameter sensitivity, and separately labeled market differences. No invented confidence score is returned.

Evaluation selects forecasts at or up to five seconds before 10m/5m/2m/1m/30s/10s horizons. Reports show both each model's availability and the common timestamp intersection. Split days chronologically 60/20/20; fewer than three days fall back to whole contracts. No fitting occurs. Report Brier/log loss (probabilities clipped only for finite scoring at 1e-15), reliability, coverage, horizons, distance and volatility strata. Uncertainty resamples complete days, never treats correlated ticks as independent trials; one-day runs return no bootstrap interval. Official outcomes are separate events, used only for numeric reconciliation and evaluation, never as model inputs.

REST snapshots without an exchange event timestamp retain null source time and explicitly label age as **snapshot receipt age**. A receipt timestamp is not invented as an exchange event time. All three provider projections are recorded separately; each carries its own effective configuration hash. Raw metadata and incoming reference frames are durably captured even when subsequent normalization is unavailable.

Readiness has separate verification, runtime-input, and model states. Valid inputs can support an explicitly assumption-based terminal research diagnostic while settlement verification remains blocked. The primary series stays null. Verified mechanics and valid inputs permit a raw estimate labeled uncalibrated; calibration remains a separate unestablished property.
