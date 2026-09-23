# Live automation

The independent execution service places real orders from published collector
signals. The fleet dashboard forwards controls and manual tickets to it. Keep
`TRADING_MODE=PAPER` and `ENABLE_LIVE_TRADING=false`; these guard the legacy
executor, not this separate real-order path.

## Controls

| Action | Effect |
| --- | --- |
| Apply **Allow new buys**, quantity and confirmation | Enables that asset's current and future markets; persists across restarts |
| Disable new buys | Stops entries; existing exits continue |
| Change quantity | Changes future buys only; integer 1–20, default 10 |
| Take manual control | Pauses the selected market, disables future asset buys and requests cancellation of its resting offer |
| Confirm a manual ticket | Pauses automation before preflight |
| Shut down safely | Disables future buys and waits for managed exposure/orders to be resolved or taken over; does not liquidate |

Applied controls cannot recall sent orders. Closing the browser or restarting the
dashboard leaves execution running. Re-enabling management resumes the same market.

## Entries

Read [crypto settings](ACTIVE_PAPER_SETTINGS.md) or [commodity settings](COMMODITIES.md)
for thresholds. Standard/late windows, fresh same-side confirmation, healthy
reference/book data, valid metadata, capped probability, price and risk checks
apply again before submission. Paper inventory/cooldowns do not describe real
holdings. Live buys require a flat real position, sufficient cash including fees,
and at most one filled bot entry per ticker.

Buys are fill-or-kill for the full selected quantity. Their limit is the configured
maximum entry price, rounded down to a supported purchased-outcome tick. The
observed ask must still pass the entry checks; better offers may fill below the
limit. Fees are additional. `max_spread=1.0` in the tracked presets removes the
spread ceiling, while quote validity and all other gates remain.

Known empty attempts can retry immediately after fresh validation, up to two
retries. Pre-submit failures do not consume that allowance. Sent orders, including
exchange rejections, do; older records without sufficient timing evidence count
conservatively. Uncertain responses block replacements until reconciled.

## Exits

| Trigger | Order behavior |
| --- | --- |
| Held-side bid ≥99¢ | Sell remaining bot holdings with a 99¢ minimum; never round this floor down |
| Held-side bid ≤55¢ | Commit to selling remaining bot holdings with a 1¢ minimum; overrides take-profit |

IOC exits are reduce-only and may partially fill. After reconciliation, retry only
the confirmed remainder, at least two seconds apart while the market stays open.
There is no sell-attempt limit. A committed stop survives rebounds, stale price
feeds and restarts; market status and actual holdings still require exchange checks.
A temporary zero-holdings response delays submission rather than clearing the exit.
Manual positions are not adopted. Triggers and limits do not guarantee fills.

### Resting take-profit

After a confirmed buy, place one 99¢ GTC sell for the confirmed quantity still held,
expiring at market close. Partial fills leave its remainder working. Rejection
leaves the IOC exits available. Kalshi does not support reduce-only GTC orders,
so placement checks holdings; external position changes can invalidate the size.
Use manual takeover before managing that position elsewhere.

A fresh bid **below 70¢** cancels the remaining offer permanently for that position;
exactly 70¢ does not. The 99¢ profit and 55¢ stop triggers remain for unsold contracts.
At either exit trigger, cancel and reconcile the resting offer before placing an
IOC for the confirmed remainder. Unknown cancellation/acknowledgment blocks a
replacement. A direct jump to the stop can still incur cancellation latency.
Manual takeover does not liquidate; safe shutdown waits for cancellation confirmation.

Protocol references: [create order](https://docs.kalshi.com/api-reference/orders/create-order-v2),
[cancel order](https://docs.kalshi.com/api-reference/orders/cancel-order-v2).

## Daily live loss guard

**Currently disabled by operator request:** the live daily-loss cutoff and its historical accounting checks do not run for any asset. `LIVE_DAILY_LOSS_GUARD_ENABLED = False` in `live_automation.py` controls this temporary suspension. Per-position stops, take profit, and other execution checks remain active. The following describes the retained behavior if re-enabled.

When enabled, each asset disables new buys at **−$20 realized bot P&L in a UTC day**, including
confirmed costs, proceeds and fees. Partial sales allocate entry costs/fees
proportionally; official settlement values the remainder. Unrealized changes,
paper fills and unrelated manual buys do not count. Manual sales of bot holdings do.

Exits continue. The saved switch stays off through restart and midnight until
explicitly re-enabled. Same-day re-enabling uses current verified P&L as the new
reference: re-enable at −$21 and the next trigger is −$41. The next UTC day returns
to the normal −$20 threshold. Re-enabling never bypasses incomplete accounting.
Mixed manual/bot purchases, missing current-day settlements, missing fill economics
or cumulative sales that cannot be allocated across midnight block new entries.

## Journal, fills and recovery

Preserve `manual-orders.sqlite` beside the fleet manifest. It holds controls,
request IDs, orders and immutable execution events. One process lock owns the
journal. Unknown orders block buys globally; confirmed resting offers block
conflicting orders in their market. Unrelated pending orders do not prevent a
confirmed reduce-only exit elsewhere.

Authenticated [fill notifications](https://docs.kalshi.com/websockets/user-fills)
are identity/quantity checked and deduplicated by trade ID, including arrivals
before POST acknowledgment. They report observed fills but do not clear unknown
orders. REST remains authoritative for terminal status and the sellable remainder.
Reconnect wakes reconciliation; REST polling continues during stream outages.
`/api/manual/status` exposes stream health; `/api/live/status` exposes exit policy.

Every 15 seconds, settlement recovery checks expired markets with confirmed bot
buys, validates official finalized evidence and records source and receipt timing.
Pending/invalid results stay pending. A later market outcome does not change a
completed sale's P&L. [Research logging](RESEARCH_LOGGING.md) documents execution
events, checkpoints, timing fields and coverage limits.

## Latency and working set

Changed eligible/ineligible decisions publish after each collector batch. A
best-effort private Unix datagram wakes execution after the committed write;
the worker rereads authoritative state. It also polls 100 ms after each cycle.
Markets remain sequential and submissions share one account lock, so this is
not a 100 ms decision-to-order guarantee.

One reused HTTP client fetches fresh holdings/balance concurrently within the
existing rate limit. No balance/holdings cache authorizes orders. Order timing
records publication/detection, coordination, preflight, POST/response and REST
confirmation. `timing.reads` separates waits, network time, retries and status;
concurrent durations overlap. `fill_confirmed_at` means REST terminal confirmation,
even for zero-fill cancellation, not exchange execution time.

Indexed reads cover the five latest controls per asset plus older unresolved
orders/unexpired bot positions. Central reconciliation is not age-limited. Latest
manual status is limited to 50 rows; history is retained. A September 16 benchmark
on a 929-order copy measured median full/current-market/unresolved reads at
12.12/0.11/0.057 ms (80 iterations), not exchange latency or endurance.

## Independent execution service

`btc15 live-execution MANIFEST` runs separately from `btc15 fleet-dashboard MANIFEST`.
The private socket is `live-execution/api.sock` beside the manifest, inside a 0700
directory. The dashboard proxies fixed manual/live routes and never retries order
submissions. An unavailable service rejects controls; an interrupted submission
remains uncertain and must be checked by its original request ID.

An old embedded-execution dashboard must not run alongside this service. Service
handoff requires reconciled exposure and an approved cutover. Use
[cloud setup](CLOUD.md) for a fresh installation and [maintenance](VPS_MAINTENANCE.md)
for updates. See [deployment status](RESEARCH_LOGGING.md#deployment-status) for logging v2.

## Dashboard history

Verified real bot fills take precedence over overlapping simulated trades in the
dashboard. Simulation-only markets retain their paper history. Live-derived rows
use confirmed fills/fees and official settlement; ambiguous mixed ownership or
incomplete accounting is excluded rather than guessed. Combined totals are not a
pure paper backtest. Original paper records and the durable live journal remain
unchanged, including dashboard-cleared real trades.

Freshness is sampled after reading publications, so a snapshot committed during
the read is not incorrectly future-dated. Actual future timestamps, stale data,
collector recovery and expired entry windows still block buys.


### Global realized-loss cutoff

The live worker disables automatic buys across all assets when the combined all-time dashboard realized net P&L reaches **-$50 or below**. This uses the same displayed trade-result accounting, including fees and the dashboard history scope; it does not include unrealized open-position losses. It is an absolute total, not a $50 drawdown from activation.

A background task calculates the total at startup and every 15 minutes, reusing unchanged history revisions. Entry authorization and enable requests check the latest result without scanning history. Missing, failed, or more-than-15-minute-30-second-old accounting blocks new automatic buys while exits continue. The cutoff is sampled, so it is not a guaranteed maximum loss.

At the threshold, the global latch and all asset/market buy permissions are committed in one transaction. Existing position exits remain active. The latch survives service restarts and UTC day changes; individual enable controls cannot bypass it. An explicit operator-requested reset is required. The prior per-asset daily $20 guard remains disabled.
