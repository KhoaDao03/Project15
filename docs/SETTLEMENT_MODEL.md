# Settlement model and source verification

Verified against public production series/market metadata on 2026-09-08. Fixtures
preserve the actual responses under `tests/fixtures/`. No authenticated feed was
available during implementation. Documentation access is not a stream test.

## Confirmed contract semantics

The active KXBTC15M market compares the final one-minute BRTI average with the
opening one-minute average, published as `floor_strike`. Current `strike_type` is
`greater_or_equal`; YES includes a tie. The secondary rules say 60 RTI prices are
averaged and rounded to two decimal places. The parser checks primary wording,
its two times against open/close metadata, source, binary payout, strike type,
rounding precision and the price grid. Unsupported wording blocks evaluation.
Unopened contracts can lack the strike/strike type; they are recorded as pending.

The trade cutoff and underlying measurement boundary are `close_time`.
`expected_expiration_time` in the captured response is five minutes later;
`expiration_time` is a week later. Using either as the simulation horizon is wrong.

Sources: [production series](https://external-api.kalshi.com/trade-api/v2/series/KXBTC15M),
[current contract terms](https://assets.kalshi.com/contract_terms/CRYPTO.pdf), and
[market lifecycle](https://docs.kalshi.com/getting_started/market_lifecycle).
The terms also address unavailable/incomplete underlying data and outcome review.
A local recorder gap does **not** establish an official data outage or imply NO:
only the authoritative Kalshi result can close a position.

## Observation grid

Kalshi documents the standard feed's quarter-hour final-minute accumulation as
`(close−60 seconds, close]`: exclude the start tick, include the close tick.
The engine uses 60 second-indexed samples. Raw source timestamps, server receive
fields and local processing receipt are all retained. Slightly fractional source
timestamps are indexed by elapsed second; duplicate slots or missing elapsed
slots reject the prediction. The feed's published aggregate is recorded for later
comparison, but not used to fill in missing individual observations.

The standard feed and its trailing average have **different** window boundaries.
The trailing average excludes the current tick and is not automatically the
settlement average. The 5 Hz channel has no final-minute aggregate; its ticks are
recorded separately, never averaged into a 300-sample settlement result.

Sources: [standard BRTI feed](https://docs.kalshi.com/websockets/cfbenchmarks-value),
[5 Hz feed](https://docs.kalshi.com/websockets/cfbenchmarks-value-5hz).

## Unresolved precision detail

“Nearest two decimal places” does not explicitly specify half-cent tie breaking.
V1 brackets half-even versus half-up where they produce different outcomes, adds
that ambiguity to the uncertainty penalty, and refuses a deterministic ambiguous
settlement classification. It does not declare either convention official. Exact
sampling offsets and equality rounding should be reconciled against recorded
published aggregates and settled contracts before validating the model.

## Current API representation

Consume `price_ranges` dynamically rather than a fixed tick or weekday schedule.
The captured market uses .001 near the extremes and .01 in its center. Fractional
contract quantities have two decimals. WebSocket subscriptions explicitly request
`use_yes_price=true`; normalization converts NO bids to NO-leg price once. V2
order payloads use a unified YES-leg price and bid/ask direction. Discovery reads
`exchange_index` from metadata rather than assuming engine zero.

Sources: [fixed-point representation](https://docs.kalshi.com/getting_started/fixed_point_migration),
[direction conventions](https://docs.kalshi.com/getting_started/order_direction),
[sharding](https://docs.kalshi.com/getting_started/exchange_sharding),
[V2 order schema](https://docs.kalshi.com/api-reference/orders/create-order-v2).

## Fees and historical data

Current series reports `quadratic`, multiplier 1. Runtime captures event fee changes
and blocks unsupported effective overrides. The coefficients remain explicit
research settings; maker fee .0175 is a conservative assumption for this initial
paper model, not a claim that every BTC15 maker fill incurs that rate. Taker .07 is
the general schedule coefficient. Entry evaluation uses a taker-plus-rounding
upper bound even for passive orders. Actual fill fees implement six-decimal trade
fee rounding and order-level balance-alignment carry/rebate. Default .01 alignment
is conservative; Direct accounts may use .0001. Account-specific fee selection and
scheduled series fee changes are not fully integrated and require validation.

Sources: [fee schedule](https://kalshi.com/fee-schedule),
[fee rounding](https://docs.kalshi.com/getting_started/fee_rounding),
[event fee changes](https://docs.kalshi.com/api-reference/events/get-event-fee-changes).

CF historical ticks are documented through the authenticated passthrough, subject
to entitlement and a higher request token cost. V1 provides an explicit download
command. Access, available history and payload normalization were not validated.
[CF REST passthrough](https://docs.kalshi.com/cfbenchmarks/rest-passthrough).
