# Live automation

## BTC backlog entry exception

BTC automatic entries may use aged book/reference/model inputs during a confirmed
processing backlog, as explicitly requested by the operator. A current status
(at most five seconds old), connected feed, nonempty queue and at least 0.5 seconds
of processing lag must establish the backlog. Only backlog and associated data-age
rejections are waived. Research logging failures already do not independently
block execution; they do not authorize bypassing unrelated failures.

The executor uses the last matching strategy decision for the current market and
configuration, and a validated collector book. During backlog, there is no normal
two-second decision/model age ceiling. The collector publishes a separate
`backlog_book` when a valid BTC book is too old for the normal display. The bypass
is re-evaluated immediately before order submission and ceases once backlog clears.
Health reporting remains unchanged; a recovering collector is not relabeled healthy.

Missing metadata, disconnection, unsequenced/invalid books, actual reference gaps,
clock errors, missing model output, market closure, price/probability rules, account
checks, live controls and loss guards still reject entries. A stale-reference/book
quality-score penalty alone may be removed; unrelated quality penalties remain.
Other assets and fresh-quote requirements for new exit triggers are unchanged.
Already committed exits retain their existing reconciliation behavior.

`timing.backlog_entry_policy` in the durable order journal records the initial and
submission-time exception, including queue depth and model/decision age, even when
optional research capture is unavailable. The trade-off is deliberate: BTC may buy
using a price or probability estimate that no longer matches the market. This policy
does not increase processing capacity or fix the metadata refresh cadence.

This change requires the updated collector and executor to be loaded; editing the
source does not reconfigure already-running processes.

The independent execution service places real orders from published collector
signals. The fleet dashboard forwards controls and manual tickets to it. Keep
`TRADING_MODE=PAPER` and `ENABLE_LIVE_TRADING=false`; these guard the legacy
executor, not this separate real-order path.

## Controls

| Action | Effect |
| --- | --- |
| Apply **Allow new buys**, quantity and confirmation | Enables that asset's current and future markets; persists across restarts |
| Disable new buys | Stops entries; existing exits continue |
| Change quantity | Changes future buys only; whole number 1–100,000 (order-system ceiling), default 10 |
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
holdings. Live buys require a flat real position and at most one filled bot entry
per ticker. Balance is not fetched during automatic-buy preflight; Kalshi enforces
funds at submission. Dashboard balance display remains available.

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
| Take-profit | Disabled (`take_profit=null`); no automatic 99¢ sale |
| Held-side bid ≤55¢ | Commit to selling remaining bot holdings with a 1¢ minimum |

IOC exits are reduce-only and may partially fill. After reconciliation, retry only
the confirmed remainder, at least two seconds apart while the market stays open.
There is no sell-attempt limit. A committed stop survives rebounds, stale price
feeds and restarts; market status and actual holdings still require exchange checks.
A temporary zero-holdings response delays submission rather than clearing the exit.
Manual positions are not adopted. Triggers and limits do not guarantee fills.

### Resting take-profit

Resting take-profit is disabled for all active assets (`take_profit=null`). No new
99¢ GTC sell orders are placed. Any existing resting take-profit order is canceled
and reconciled before a replacement stop sale. Uncertain cancellation blocks a
replacement until the remaining holdings are confirmed. Positions otherwise stay
open until the 55¢ stop triggers or exchange settlement.

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

### Dedicated stop monitor (deployed 2026-09-25)

The executor runs an independent asynchronous stop monitor for confirmed bot
holdings. Collector book publications send a best-effort quote notification after
the database write commits; the monitor also polls every 100 ms. Publications
retain the collector's existing approximately 50 ms display cadence, so this is
not a guarantee of observing every exchange tick or reacting within 100 ms.

A fresh held-side best bid at or below the configured stop (55¢ in the active
controls) commits `HARD_STOP` to the durable control before any network wait.
Committed stops survive rebounds, stale quotes and restarts. Detection in other
markets continues while an exit awaits the exchange. Per-market locks coordinate
the normal loop, stop submissions and central reconciliation. Queued stop
submissions precede queued buys at the shared account lock; requests already in
progress are not interrupted.

Hard-stop submission performs one fresh series/market/holdings preflight, instead
of fetching these both in the market loop and again in submission. Sell quantity
is bounded by both the bot's journal remainder and actual holdings. The reduce-only
IOC order retains its 1¢ floor, final authorization, uncertain-order handling and
two-second spacing between sent exit attempts. Zero holdings does not clear the
stop or consume a sent-attempt cooldown. Manual takeover still blocks submission.

Order timing includes `stop_detected_at`, `stop_bid`, `quote_received_at` and
`quote_published_at` when available, alongside existing POST timing. This path
still inherits collector lag and the existing REST rate limiter. No direct
exchange quote connection is added. Deployed on 2026-09-25 at 00:19:38 UTC;
see the [latency and deployment report](../reports/stop-latency-20260925/report.md)
for measurements and their limits.

### Burst read budget and buy balance removal (deployed 2026-09-25)

The implementation replaces per-client 200 ms spacing with a token bucket
shared by credentialed clients under the same OS user and API origin. It uses the
account limits verified on 2026-09-25: 200 tokens/second and 600-token capacity.
Most reads cost 10 tokens; CF Benchmarks reads cost 50; individual order-status
reads cost 2. Other discounted endpoints conservatively cost 10 locally.

A private `/tmp/btc15-read-budget-UID/` file and short nonblocking file locks account
for collectors, executor and dashboard reads together. Network calls and sleeps
never hold the lock. New, corrupt or previous-boot state starts with zero credit;
process restarts reuse existing credit rather than allocating another bucket.
Anonymous clients without account credentials use an in-memory budget. Different
OS users, hosts and external clients are outside this coordination boundary.

Available credit permits immediate bursts; exhausted credit waits for refill.
Ordinary reads leave 30 tokens for hard-stop preflight, and the existing queued-stop
submission priority remains. A 429 discards local credit and imposes shared bounded
backoff; retries still consume tokens, remain bounded and are included in timing.
No automatic POST retries are added. Limits/costs must be rechecked before deploying
this version to a different account or after an exchange rate-policy change.

Automatic buy preflight now reads series, market and fresh holdings, then rechecks
controls before POST. It does not fetch account balance or locally authorize cash
sufficiency. Kalshi determines whether funds cover an order. Balance display and
manual ticket context remain available. Sent insufficient-funds rejections retain
the existing entry-attempt accounting and retry limit. Research preflight records
explicitly mark `balance_check=not_performed`; no balance value is fabricated.

Deployed on 2026-09-25 at 05:17:47 UTC with a coordinated restart of all seven
collectors, executor and private dashboard. See the
[deployment report](../reports/read-budget-deployment-20260925/report.md).
Mixing old per-client limiters with the shared budget does not provide full fleet
accounting.

### Entry loop and shared transport

Changed eligible/ineligible decisions publish after each collector batch. A
best-effort private Unix datagram wakes execution after the committed write;
the worker rereads authoritative state. It also polls 100 ms after each cycle.
Markets remain sequential and submissions share one account lock, so this is
not a 100 ms decision-to-order guarantee.

The executor reuses one HTTP client, fetches fresh holdings without a balance
preflight, and shares its read budget with collectors. No holdings cache authorizes
orders. Order timing
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

### Disabled stop policy

A live control with `stop_price=0` disables new hard-stop triggers, including at
a zero bid. Gold uses this policy from October 6, 2026. Paper configurations must
set both `fixed_stop_price=0` and `stop_multiplier=0` to disable price stops.
Previously committed stops remain committed. Other assets retain positive stops.

### Scheduled entry blackouts (October 6, 2026)

Automatic live buys for BTC, ETH, SOL, XRP, BNB, HYPE and DOGE are blocked Monday–Friday 11:45–13:00,
Tuesday additionally 20:00–21:15, Thursday additionally 19:45–21:00, and
Saturday–Sunday 11:15–12:30. All times use America/New_York, including daylight
saving changes. Start boundaries are inclusive; end boundaries are exclusive.
The executor checks the schedule on entry and again after preflight immediately
before submission. BTC backlog exceptions cannot bypass it. The dashboard/research
rejection is ENTRY_TIME_BLACKOUT. Exits, settlement and manual tickets are
unaffected. Gold, silver and WTI do not use these blackout windows. Entry permission resumes automatically after the window, subject to
the existing controls and other filters. Orders already submitted cannot be recalled
by this check. Signal collection and paper research continue during these windows.
