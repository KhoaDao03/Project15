# Manual real-money trading

The shared `fleet-dashboard` has a **Buy / sell real** button and a dedicated
**Buy / sell BTC / ETH / SOL / XRP · real** button above each asset card. Each asset
button selects that asset’s current contract in the ticket. It opens a separate
Kalshi production order ticket for BTC, ETH, SOL and XRP 15-minute contracts.
The paper strategy, paper balances and paper performance totals remain simulated;
manual positions are not adopted by the bot. Separately, [per-asset live automation](LIVE_AUTOMATION.md) can submit real orders after explicit opt-in. Confirming a manual order pauses automatic buys and exits for that market, even if that manual order subsequently fails validation.

Choose a contract, YES/NO, contract quantity (increments of 0.01, where the market supports fractions) and limit price in cents
(0.01–99.99, subject to the market’s tick grid). Review the buy or sell, then click **Confirm real-money order**. The review
shows the exact contract, side, quantity, limit, and purchase cost or sale proceeds
before fees. Account/quote reviews expire after 30 seconds. Refreshing or changing
an input requires a new review. The ticket reads real holdings and the balance
for the contract's exchange index, using the configured primary account.

Buys use the limit as a maximum price; sells use it as a minimum. A 1¢ sell limit
permits fills at 1¢ or better, including lower bids after better liquidity is
exhausted. It is not a guarantee of the currently displayed bid for the whole
quantity. Buy review defaults an empty limit to 95¢; sell review defaults to the
current bid. The user can edit either limit, including supported sub-cent prices.

Buys are fill-or-kill limits: the exchange fills the entire requested quantity
at available prices no higher than the cap, or fills none. Cheapest offers match
first; the cap is not a fixed execution price. Sells remain immediate-or-cancel:
partial fills are possible and the remainder cancels. Sells require sufficient real holdings and set the
exchange's `reduce_only` flag. Buying against an opposite-side real position is
rejected; use Sell on the held side first. The manual ticket has no resting orders, automatic stop loss, automatic exit, subaccount selector, or real portfolio P&L dashboard. Automatic exits apply only to separately enabled bot positions. Closing the UI or stopping the
paper collectors does not liquidate real positions.

## Setup and scope

The independent execution service uses the existing `KALSHI_API_KEY_ID` and
`KALSHI_PRIVATE_KEY_PATH` from its server environment / `.env`. The dashboard forwards requests over a private local socket and never submits to Kalshi directly. The API key must
permit trading on the primary production account. Credentials never go to the
browser. Do not paste them into chat. Keep `TRADING_MODE=PAPER` and
`ENABLE_LIVE_TRADING=false`: these guard the legacy collector execution path. The manual ticket and explicitly confirmed per-market live controls use separate dashboard order submission. A standalone single-asset dashboard does not expose the manual
order API; use `fleet-dashboard` and its manifest.

The server verifies the series and market's exchange index instead of using the
index from API examples. V2 orders quote the YES book: buy YES / sell NO use bids;
sell YES / buy NO use asks, with NO prices converted to `1 - price`. Sell orders
always set `reduce_only=true`. Local same-origin JSON confirmation is required.

## Order activity and uncertain results

The ticket's activity list records submissions, exchange status and filled
quantity. A separate `manual-orders.sqlite` beside the fleet manifest stores the
request ID, instructions, acknowledgement and exchange result. This file is not
a paper ledger. Keep it when restarting the dashboard.

Each reviewed request has a unique ID. Repeating it returns the recorded request;
changing its instructions is rejected. The server persists the request before
sending it and never retries a POST. A timeout, server error or ambiguous
acknowledgement may mean the exchange accepted the order. New manual submissions
are blocked while one remains unresolved. **Check order status** performs reads
only and matches the original ID. Absence from an order listing is not treated
as proof of rejection. If it cannot be resolved, inspect the order in Kalshi and
retain the journal for diagnosis; do not delete the journal to retry blindly.

## Verification

Order submission tests use a mocked exchange, covering all YES/NO buy/sell
conversions, exchange-index routing, position limits, validation, confirmation,
partial fills, duplicate requests and unresolved outcomes across restarts.
Production verification is GET-only account/market access; no real test trade
is submitted. A read-access check cannot prove that the API key has order-write
permission or that a future order will fill.

References: [Kalshi Create Order V2](https://docs.kalshi.com/api-reference/orders/create-order-v2),
[Get Positions](https://docs.kalshi.com/api-reference/portfolio/get-positions),
[Get Balance](https://docs.kalshi.com/api-reference/portfolio/get-balance),
[orderbook responses](https://docs.kalshi.com/getting_started/orderbook_responses),
and [limit order sales](https://help.kalshi.com/en/articles/13823815-limit-order-sale).

The shared dashboard displays **Real account · cash available** above the paper
portfolios. This is the Kalshi primary account's available cash across exchange
indexes, excluding the value of held contracts. It refreshes every 30 seconds,
after a manual submission attempt, and when **Refresh balance** is clicked.
Unavailable reads display a dash instead of retaining an apparently current balance.

Each asset card has two separate buy sections: green **YES** and rose **NO**,
each with its own contract quantity and **Buy YES/NO · max 95¢** button.
A shared sell section has a separate quantity, YES/NO selector, and
**Sell now · min 1¢** button. Outcome labels remain explicit alongside the colors.
These shortcuts fetch fresh account/quote data and open the existing review.
Buy defaults to a reviewable 95¢ cap with the full requested quantity or no fill.
Sell uses a 1¢ floor. No order is sent until the user confirms the real-money order.

Each asset's buy controls include a **Bought this market** box with separate YES
and NO totals. It sums confirmed cumulative buy fills from the dashboard's real
order journal for the currently displayed market, across all dashboard orders.
Unfilled quantities and paper trades are excluded; selling does not reduce the
cumulative purchased count. The box switches to the new market at rollover.
Pending orders are marked because their final fill totals may not be known yet;
use Check order status to reconcile them. Orders placed outside this dashboard
are not included. Unavailable journal data displays unavailable, not zero.
