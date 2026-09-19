# Per-asset live automation

The independent live execution service trades real Kalshi contracts using the current paper strategy's published entry decisions. The shared fleet dashboard forwards user controls and manual orders to that service. Paper portfolios and collectors remain simulated. Keep `TRADING_MODE=PAPER` and `ENABLE_LIVE_TRADING=false`; explicitly confirmed dashboard controls authorize this separate real execution path.

## Controls

Select **Allow new buys**, enter an integer quantity from **1 to 20**, click **Apply live settings**, and review the real-money confirmation. The setting applies to all current and future markets of that asset until switched off, and survives dashboard restarts. Each asset has its own switch. Quantity defaults to 10 and the server enforces 20 maximum. Quantity changes affect future buys, not existing holdings.

Turning the switch off and applying stops buys but continues exits. **Take manual control** pauses both for the selected market and disables future buys for that asset. Confirming a manual order also pauses automation before preflight. Edits take effect when applied or confirmed; they cannot recall orders already sent. Re-enabling resumes management of the same market.

## Daily live loss guard

Each asset independently disables new live buys when its accumulated realized bot
P&L reaches **−$20 or below during a UTC calendar day**. The calculation uses
confirmed live order costs, sale proceeds and fees, allocating entry costs and
fees proportionally to partial sales. Official settlement values the remaining
contracts. Paper fills, unrealized changes and unrelated manual purchases do not
count. Manual sales of bot holdings count; mixed manual/bot purchases or incomplete
fill accounting block new entries until the P&L can be verified.

The guard runs in the live execution service, including immediately before a buy
and when enabling live settings. It turns off the saved asset switch and its
market controls, and exposes the reason in both dashboards. Existing exits keep
running. The switch stays off across restarts and midnight until explicitly
re-enabled through the confirmed live settings controls. Same-day re-enabling
resets the loss reference to the asset's current verified realized P&L: another
$20 of net realized losses disables new buys again. For example, re-enabling at
−$21 sets the next trigger at −$41. The reference persists across restarts and
settings edits; on the next UTC day the normal −$20 daily threshold applies.
Re-enabling does not bypass missing or incomplete P&L accounting.

Missing settlement of a current-day expired live position blocks new entries.
An order's cumulative sales spanning UTC midnight cannot be assigned accurately
to a day from this journal; such accounting also blocks entries rather than using
an assumed P&L. These data blocks leave automatic exits active.

## Official results and exit times

The live executor checks expired markets with confirmed bot buys for missing
official results every 15 seconds, independently of paper simulation. It validates
final REST market metadata and records the official settlement timestamp and
evidence in the current run, including after a collector cutover. Pending or
invalid exchange results remain pending. Existing exit management runs separately.

Both trade displays show the final sale or settlement timestamp. The public
view labels the line **Sold / exit** or **Settled**; a sold trade’s market outcome
can be recorded later without changing its realized sale P&L.

## Execution

All seven assets (BTC, ETH, SOL, XRP, GOLD, SILVER, WTI) start considering entries with 7 minutes remaining. Standard entries run until 2 minutes remaining, and late entries until 1 second remaining (exclusive). Both require one fresh confirmation sample. Entry requires fresh, healthy collector decisions and books. Current strategy settings require Bleep confidence of at least 83% for crypto and 85% for commodities after safety and market-respect caps, asks between 80 and 95 cents, and the remaining strategy entry filters. Paper inventory and its post-close cooldown are excluded because they do not describe real holdings. Live entry requires a flat real position in the market, sufficient cash including a fee allowance, and at most one filled bot entry per ticker.

Buys are fill-or-kill for the selected quantity: all contracts or none. The limit uses the configured maximum entry price plus one cent of execution headroom (currently 96 cents), capped at 99 cents and rounded down to a supported tick in the purchased outcome's price. The observed ask must still pass the strategy entry range and all other entry checks. Cheaper offers can fill first; fees are additional. Known empty attempts retry immediately after confirmation and fresh entry revalidation, with no added cooldown (at most two retries). Network and exchange processing time still applies. Uncertain acknowledgements never trigger blind replacements.

Confirmed pre-submit rejections (for example, stale data, a changed signal or insufficient cash) do not consume the entry-attempt allowance. They return to the worker loop for fresh validation. Submitted orders still count, including exchange rejections; uncertain submissions block replacements until reconciled. Older journal records without enough timing evidence remain conservatively counted. This classification also applies after restart without deleting order history.

Live exits retain two trigger rules:

- **Hard stop:** a held-side bid at or below **55 cents** commits the bot to selling its entire remaining bot position with a **1-cent minimum**. Reduce-only immediate-or-cancel orders may partially fill. After reconciliation, the bot retries only the confirmed remainder at least two seconds apart while the market remains open, with no sell-attempt limit. Once triggered, the exit persists through price-feed outages and restarts; market status and real holdings are still checked through the exchange API. A temporary zero-holdings response delays submission and is rechecked instead of permanently pausing the exit.
- **Take profit:** a bid at or above **99 cents** starts selling with a **99-cent minimum**. This floor is never rounded down; an unsupported venue tick is rejected. A subsequent hard-stop trigger overrides take profit and uses the 1-cent stop floor.

These two live rules are independent of other paper-only exits. Sells are capped by the bot's confirmed remainder and actual exchange holdings, with `reduce_only`. Manual positions are not adopted. If external trading leaves zero holdings, the bot sends no sell and keeps rechecking until the market closes or the user takes manual control.

## Resting 99-cent take-profit (September 15)

After confirming a buy, the worker submits one reduce-only, good-till-canceled sell at 99¢ for the confirmed quantity still held. The journal persists the order identity and cumulative fills. It checks fills through the authenticated fill stream and REST reconciliation, including after a restart; partial fills leave the remaining offer working. A rejected resting placement leaves the original IOC exits available.

A fresh held-side **bid below 70¢** cancels the remaining resting offer. Exactly 70¢ does not trigger cancellation. This cancellation is permanent for the position: rebounds do not recreate the resting order. The original 99¢ profit and 55¢ hard-stop triggers remain active for unsold contracts.

At a 99¢ profit trigger, the worker cancels/reconciles any remaining resting offer before sending the original 99¢ IOC for the confirmed remainder. At or below 55¢, it commits the original 1¢-minimum stop, cancels any still-active resting offer, then reconciles final fills and sells only the remainder. A stop remains committed through feed loss, failed cancellation attempts, rebounds and restarts. An uncertain cancellation or order acknowledgement blocks replacement until the exchange confirms the outcome. A direct jump through 70¢ to the stop can still incur cancellation latency.

Manual takeover also requests cancellation of the bot's resting offer; it does not liquidate the remaining holdings. Safe shutdown waits for cancellation confirmation even after takeover. Confirmed working offers in other markets do not block entries, while ambiguous submissions continue to do so. Cancellation timestamps are stored as `cancel_requested_at` and `cancel_attempted_at`; `/api/live/status` exposes `exit_policy` for runtime verification.

Cancellation uses the [Kalshi V2 cancel endpoint](https://docs.kalshi.com/api-reference/orders/cancel-order-v2), followed by an authoritative order-status read because the cancellation response does not contain final cumulative fills. The bot never sends an independent duplicate full-size sell alongside a resting offer.

## Persistence and limitations

The live execution service must stay running. The dashboard can stop or restart without stopping order management. Fresh collector data is required for entries and new exit triggers; already triggered exits can continue without fresh price quotes while the exchange API is reachable. Closing the browser does not stop automation. Safe shutdown refuses to stop feeds while a bot position or unresolved order remains managed; close it or explicitly take manual control first. Safe shutdown also switches off future buys; an ordinary service restart preserves the switches.

Preserve `manual-orders.sqlite` beside the fleet manifest across restarts: it contains controls and the shared journal. One process lock permits one live worker per journal. Unique request IDs are persisted before submission. Timeouts and ambiguous responses are reconciled by reads before replacements; confirmed partial fills survive restarts. Uncertain orders block new buys globally; confirmed resting take-profit orders only block conflicting orders in their own market, but an unrelated pending order does not block a confirmed reduce-only exit in another market.

Submission acceptance and execution cannot be guaranteed: connectivity, exchange availability, liquidity, market closure, tick rules and account permissions still apply. Fill-or-kill specifies all-or-none behavior, not a guaranteed fill. A 1-cent sell floor permits execution down to 1 cent and does not guarantee the displayed bid for the whole position.

Tests use a mocked exchange for partial fills, restart recovery, unknown acknowledgements, controls changing during preflight, quantity limits and both outcome conversions. Deployment checks are read-only; no real test order is submitted.

Reference: [Kalshi Create Order V2](https://docs.kalshi.com/api-reference/orders/create-order-v2).

## Submission latency and fill reporting

The live worker checks again 100 ms after each completed cycle (previously 500 ms).
Exchange work can extend the cycle: markets remain sequential and submissions share
one account lock. Fill notifications also wake the worker. This is not a guaranteed
100 ms decision-to-order bound.

The execution service reuses one HTTP client and its existing read-rate limiter, closing it
on shutdown. Automatic buys fetch series/market metadata once, snap the limit within
the approved cap, and fetch fresh holdings and balance concurrently. Market identity,
tick grid, cash, flat holdings, control revision and final strategy freshness checks
remain enforced. No account balance or holdings cache is used. The shared read budget
still spaces requests; concurrency overlaps network waits without bypassing limits.

Order journal `timing` fields record decision publication/detection, submission request,
preflight start/completion, POST start, response receipt and REST confirmation.
`coordination_wait_ms`, `preflight_ms`, and `submission_ms` use a monotonic clock.
Wall-clock fields permit comparison with exchange timestamps but are sensitive to
clock skew. Rejected/uncertain attempts only contain stages actually reached.
`fill_confirmed_at` denotes REST terminal confirmation, including canceled orders
with no fills; it is not the exchange execution time. `effective_limit_cents` records
the tick-snapped bot limit while the original request retains its approved cap.

An authenticated [Kalshi fill stream](https://docs.kalshi.com/websockets/user-fills)
records notification arrival and cumulative observed fill quantity on known journal
orders. The order activity panel shows this separately from REST-confirmed quantity.
Client/order identity, ticker, exchange, primary subaccount, action, outcome and quantity
are checked; duplicate trade IDs do not count twice. Notifications arriving before the
POST acknowledgement are retained. Stream health is exposed by `/api/manual/status`.

Notifications do not clear uncertain orders or authorize replacements. REST remains
authoritative for terminal status and the remainder available to sell. Stream reconnects
wake reconciliation; regular REST reconciliation continues during outages. No real
orders are sent by the regression tests. An execution service restart is required to
activate submission changes; paper collectors do not need restarting.

### Immediate decision publication and request measurements

Eligible decisions no longer wait for the collector's one-second status publication.
After a processing batch, a changed candidate (including a paper `NO_TRADE` whose only
blocks are paper inventory/cooldown) publishes the current book projection and decision.
A subsequent ineligible decision is also published promptly, so a previous candidate
is not kept eligible by a slow status cadence. Final live authorization still rereads
health, strategy and current book state.

After the committed write, the collector sends a best-effort Unix datagram to the live
worker. The socket is identified by the resolved SQLite database path, inside a private
per-user directory under `/tmp`. Messages carry no trading instructions and only wake
the worker, which rereads the authoritative ledger. Missing/full sockets cannot block
collection. The live worker binds listeners while holding its process lock and removes
them on shutdown; its 100 ms fallback polling and account-wide order lock remain.
Notifications cannot interrupt an already-running REST request within execution; dashboard
work runs in a separate process, and do not guarantee a fill or a fixed decision-to-order latency.

Each order's `timing.reads` records REST endpoint, start time, attempts, total elapsed
milliseconds, rate-limit/lock wait, network wait, retry backoff, final HTTP status and
error type when applicable. These measurements are scoped to the order submission and
its immediate reconciliation, including concurrent holdings/balance tasks. They do not
record authentication headers, credentials, query values or response bodies. Concurrent
request durations overlap and should not be summed as total preflight latency.

Activation of this publication change requires restarting the affected paper collectors
and the execution service; unlike the earlier HTTP-only improvements, both sides changed.

## Live buy execution headroom

Live entry signals still require the selected-side ask within the configured entry range (currently 80–95¢). Live buy orders allow one cent above the configured maximum (currently a 96¢ limit), capped at 99¢. The quoted-price filter is checked again before submission. Orders can fill at available prices up to 96¢; this is a maximum, not a forced purchase price or a fill guarantee. Full-quantity fill-or-kill behavior is retained. Paper execution is unchanged.

### Routine market window

The live worker cycles through the five most recent market controls per asset
(BTC, ETH, SOL and XRP), ordered by market close time. This is a processing window,
not a deletion or a limit on trade history. Older unresolved bot orders and older
unexpired bot positions remain included for safety. Central reconciliation still
checks unresolved orders regardless of this window. Per-market checks and the final
submission authorization use a direct ticker lookup instead of reloading all controls.

## Independent execution service

Run `btc15 live-execution data/runtime/crypto-dashboard.json` separately from
`btc15 fleet-dashboard data/runtime/crypto-dashboard.json --port 8000`.
The installed `btc15-live-execution.service` starts independently at user-service boot
and restarts on failure. It has no lifecycle dependency on the dashboard.

The execution process owns automated entry/exit logic, manual submissions, fill-stream
handling, reconciliation, live settings and safe-shutdown authorization. The dashboard
only proxies `/api/manual/*` and `/api/live/*` to `live-execution/api.sock` beside the manifest.
The socket directory has mode 0700, making the Unix socket private to the service user even when the HTTP server sets socket mode 0666. Browser requests still pass
local-host, same-origin and JSON checks before forwarding; internal routes retain the
same validation. The existing `manual-orders.sqlite` journal and `.live.lock` are reused.
A second execution process fails startup if that lock is owned. Request IDs and persisted
controls survive process restarts. The proxy never retries order submissions.

If execution is unreachable, the dashboard displays execution unavailable and rejects
order/control actions. An interrupted order request is explicitly uncertain: inspect
its original request ID, rather than submit a replacement. Quotes and paper statistics
remain available. `Stop all safely` asks execution to disable buys and confirm no
managed positions/unresolved orders before stopping collectors. Merely closing or
restarting the dashboard does not disable live trading.

Deployment handoff: confirm no unresolved orders/unexpired managed holdings, stop the
old dashboard that owns execution, start the independent service, then start the new
dashboard. Do not run an old embedded-execution dashboard alongside the new service.
Verify service PIDs, persisted controls, live status and continued worker cycles across
a dashboard restart; no real test order is necessary. Collectors need no restart.

### Indexed order working set

The execution loop reads orders by indexed market ticker and unresolved state.
It loads filled-order history only for unexpired bot markets when finding older
positions; unresolved orders remain eligible regardless of age or the five-market
window. Each read sees persisted state, including changes made during awaited
exchange requests. Order-ID reuse and account-wide pending-order checks remain
inside the same write transaction before submission.

Individual status/cancellation requests use order-ID lookups, the manual status
list reads only its latest 50 rows, and dashboard purchase totals read only the
required fields while retaining exact decimal arithmetic and all historical fills.
No journal records are removed. Indexes are created on execution-service startup.

A September 16 offline benchmark against a copy of the 929-order journal measured
median full reads at 12.12 ms, current-market reads at 0.11 ms, and unresolved reads
at 0.057 ms (80 iterations). These are database timings, not exchange latency or
an endurance-test result. Existing input-sensitive model evaluation, background
historical statistics and batched collector recording are unchanged.

## Dashboard live fallback

When a market has no filled paper purchase, confirmed bot fills from the durable live order journal appear in the paper dashboard's trade history, open-trade count and performance totals as **Live fallback**. Existing historical fills are included automatically. If paper has any buy fill or completed trade for that market in the selected run, it takes precedence, so both executions are not counted. Failed and unconfirmed orders do not create purchases.

Fallback exits and fees use confirmed exchange records; partial sales retain the remaining position. A recorded official settlement closes any remainder. Markets with mixed manual and bot purchases, missing fill economics or unallocatable sales are excluded rather than guessed. These totals combine simulated and live-derived outcomes and must not be described as a pure paper backtest.

This is a dashboard-only view reconstructed from the persistent journal, not a new paper execution. It survives dashboard restarts without copying or modifying immutable paper records, collector checkpoints, risk state or live controls.

Freshness checks sample wall-clock time after reading collector publications. A
quote or decision published during preceding database reads must not be rejected
as future-dated relative to the start of the check. Actual future timestamps,
stale publications, collector recovery, and entry-window expiry still block buys.
