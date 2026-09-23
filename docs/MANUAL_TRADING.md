# Manual real-money orders

Use **Buy / sell real** in the fleet dashboard. An asset-card button selects its
current contract. The separate executor places production orders for supported
crypto and commodity assets; a standalone single-asset dashboard has no manual API.
Manual positions are not adopted by the bot. Confirming a ticket pauses that
market's automatic buys/exits even if preflight later rejects it.

## Review and submit

Choose the contract, YES/NO, quantity and limit price, then review and explicitly
confirm the real-money order. Fractional quantities use 0.01 increments where the
market permits; prices are 0.01–99.99¢ subject to its tick grid. Reviews show cost
or proceeds before fees and expire after 30 seconds. Changing inputs requires a
new review. Holdings and cash come from the configured primary account/exchange.

| Order | Limit and execution |
| --- | --- |
| Buy | Maximum price; fill-or-kill for all requested contracts or none |
| Sell | Minimum price; reduce-only IOC may fill partly and cancel the rest |

Empty buy limits default to 95¢; ordinary sell review defaults to the bid. Asset
shortcuts use **Buy YES/NO · max 95¢** and **Sell now · min 1¢**, but still open a
review and require confirmation. Limits are editable. A 1¢ sell permits fills down
to 1¢; it does not promise the displayed bid for the full quantity. Fees are extra.

Sells require actual holdings. Buying against an opposite-side position is rejected;
sell the held side first. The manual ticket has no resting order, automatic exit,
stop loss or subaccount selector. Closing the UI does not liquidate holdings.

## Setup and scope

The execution service loads `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH` on the
server; credentials never enter the browser. Its key must permit production trading.
Keep `TRADING_MODE=PAPER` and `ENABLE_LIVE_TRADING=false` for the legacy guard.
The dashboard forwards confirmed same-origin JSON requests over a private socket.

The server validates the actual series/exchange index. V2 uses YES-book prices:
buy YES/sell NO are bids; sell YES/buy NO are asks; NO prices convert to `1 - price`.
Sells set `reduce_only=true`. See [live automation](LIVE_AUTOMATION.md) for separate
bot-managed orders and [cloud setup](CLOUD.md) for service installation.

## Order activity and uncertain results

Preserve `manual-orders.sqlite` beside the manifest. It stores request IDs,
instructions, acknowledgments, status/fills and execution events. Repeating an ID
returns the recorded request; reusing it with changed instructions is rejected.
The request persists before transmission, and POSTs are never blindly retried.

Timeouts/server errors/ambiguous acknowledgments may hide an accepted order.
Unresolved requests block new manual submissions. **Check order status** performs
reads against the original ID; absence from an order list alone is not proof of
rejection. Reconcile before replacing. [Research logging](RESEARCH_LOGGING.md)
explains individual fill evidence and timing limitations.

## Display totals

**Real account · cash available** excludes held contracts and refreshes every
30 seconds, after submission attempts and on manual refresh. Unavailable reads show
a dash. **Bought this market** sums confirmed YES/NO buy fills from this dashboard's
journal; sells do not subtract from it. It excludes paper fills and external orders,
changes at rollover, and marks unresolved quantities rather than assuming zero.

## Verification

Tests use mocked transports; deployment checks use read-only account/market access.
A successful read proves neither order-write permission nor future fills.
Protocol references: [create order](https://docs.kalshi.com/api-reference/orders/create-order-v2),
[positions](https://docs.kalshi.com/api-reference/portfolio/get-positions),
[balance](https://docs.kalshi.com/api-reference/portfolio/get-balance),
[order books](https://docs.kalshi.com/getting_started/orderbook_responses).
