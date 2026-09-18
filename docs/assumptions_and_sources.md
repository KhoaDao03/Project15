# Probability engine: assumptions and sources

> **2026-09-17 audit update:** See [settlement verification](../SETTLEMENT_VERIFICATION.md). The close-inclusive final-minute feed helper does not match the two captured published settlement values. The probability engine now uses the reviewed preceding-60-second profile, retains the half-cent tie blocker, and records live BRTI data. Historical statements below describe earlier behavior; legacy trading code was not changed by this focused audit.

Review date: **2026-09-17 UTC**. This log belongs to the isolated `btc_probability` package. Documentation review, a public HTTP request, and an authenticated integration test are different evidence.

## Source review

| Source | URL | Finding / status |
|---|---|---|
| HYHY | https://hyhy.app/ | Browser retrieval failed. No proprietary implementation inferred. |
| Author Part 1 | https://www.reddit.com/r/PredictionMarkets/comments/1webj51/i_priced_kalshis_15min_btc_market_like_an_option/ | Read as inspiration only. Discusses options smile, short-horizon adjustments, and a roughly 30-hour anchor. Claims are not independently validated. |
| Author Part 2 | https://www.reddit.com/r/PredictionMarkets/comments/1wg2n7s/part_2_making_a_trade_out_of_the_model/ | Reviewed as downstream trading context. Its thresholds are not engine parameters. |
| Crypto overview | https://help.kalshi.com/en/articles/13823838-crypto-markets | Describes averaging sixty one-second CF reference observations. General access wording conflicts with the entitlement restriction below; access is not assumed. |
| CF WebSocket | https://docs.kalshi.com/websockets/cfbenchmarks-value | Authenticated BRTI channel. Raw values, trailing average, and final-minute aggregate are distinct. Final-minute window excludes its start and includes quarter-hour close; 1–60 counts documented. |
| CF REST | https://docs.kalshi.com/cfbenchmarks/rest-passthrough | Authenticated `/cfbenchmarks/values` and `/cfbenchmarks/history/values`; account entitlement required. Historical requests support HOUR and timestamp. No entitlement tested in this delivery. |
| Binary book | https://docs.kalshi.com/api-reference/market/get-market-orderbook | Fixed-point dollar bid ladders for YES/NO; asks are opposite-side complements. Documentation requires auth, while public behavior must be tested independently. |
| Book updates | https://docs.kalshi.com/websockets/orderbook-updates | Snapshot/delta fields, subscription/sequence identity. Snapshot resets after invalidation; initial live collector uses REST snapshots. |
| Environments | https://docs.kalshi.com/getting_started/api_environments | Uses production `external-api.kalshi.com` for REST and `external-api-ws.kalshi.com` for WebSocket. |
| Lifecycle | https://docs.kalshi.com/getting_started/market_lifecycle | Trading close, resolution and final settlement are distinct stages. |
| Index | https://docs.kalshi.com/llms.txt | Consulted for current documentation pages. |
| Market metadata | https://docs.kalshi.com/api-reference/market/get-market | Market metadata supplies rules, status, target candidates, and lifecycle times. |
| Series metadata | https://docs.kalshi.com/api-reference/market/get-series | Series metadata supplies family, frequency, source, and contract URLs. |
| Generic CRYPTO terms | https://assets.kalshi.com/contract_terms/CRYPTO.pdf | Defines strict above versus inclusive at-least; generic template requires market-specific parameters. Does not resolve observed nearest-cent tie ambiguity. |
| Deribit instruments | https://docs.deribit.com/api-reference/market-data/public-get_instruments | Dynamic instruments, currencies, strikes, expiries, option types. Respect documented instrument discovery rate limit. |
| Deribit books | https://docs.deribit.com/api-reference/market-data/public-get_order_book | Distinct mark/bid/ask IV, underlying price/index, book timestamps and liquidity. |
| Deribit IV convention | https://deribitexchange.gitbooks.io/deribit-api/content/rpc-endpoints.html | Legacy official endpoint description explicitly describes IV in percent. Current captured mark IV 95.91 is normalized to 0.9591; tested against retained response. |
| Inverse option convention | https://support.deribit.com/hc/en-us/articles/31424939096093-Inverse-Options | Native inverse-option currency must not be treated as USD. This engine consumes reported IV, not a price inversion. |
| Black formulas | https://strata.opengamma.io/apidocs/com/opengamma/strata/pricer/impl/option/BlackFormulaRepository.html | Black option pricing reference; terminal digital implemented transparently and checked against the user's formula fixtures. |

## Concrete captured responses

`tests/probability/fixtures/` contains original public responses with URL and UTC retrieval timestamp:

- `kalshi_series.json`: HTTP 200 from `/trade-api/v2/series/KXBTC15M` at 05:42:08 UTC. Frequency `fifteen_min`; BRTI family confirmed.
- `kalshi_markets.json`: HTTP 200 for series-filtered markets, three upcoming contracts. Their targets were TBD; no target was invented. Primary wording compares the final 60-second average **at least** to an opening 60-second average. Secondary wording specifies nearest two decimals, without a tie rule. `expected_expiration_time`, `expiration_time`, and `close_time` differ.
- `deribit_instruments.json`: HTTP 200 from public option discovery. Retains complete returned chain, not hand-selected fake instruments.
- `deribit_book.json`: HTTP 200 for an actually discovered instrument. Mark IV 95.91, bid IV 0 and ask IV 178.9 illustrate why a successful request alone does not produce a quality-approved volatility anchor. This quote is rejected by nonpositive-IV checks.

Sandbox DNS initially failed; the successful public requests ran with approved network access. Requests used no trading credentials. Captured fixtures are data evidence, not evidence of an authenticated reference stream or usable current surface.

## Explicit modeling assumptions

Zero short-horizon carry (`F=S`), discount one, 365-day year, zero arithmetic drift, constant sigma path dynamics, constant variance-rate extrapolation from longer options, linear mark-IV interpolation in log forward moneyness, and flagged flat strike extrapolation are research assumptions. The 30-hour preference is configurable, not an optimum. Fixed 20% sigma is diagnostic only. No coefficients are reverse engineered from a screenshot. Calibration remains null.

Synthetic contracts use explicit half-up cent rounding and an exact 60-point schedule for tests. These are synthetic semantics, not a claim of verified real tie behavior.

## Unresolved and fail-closed boundaries

1. Nearest-cent half-tie convention, exact timestamp alignment, target-field authority, and observation-end mapping need a reviewed binding for each live contract. Without that, live probabilities are UNAVAILABLE.
2. Authenticated BRTI stream entitlement and REST historical payload shapes are unverified here. The historical parser accepts only recognizable value records and otherwise retains raw data without inventing observations.
3. A metadata terms URL is hashed with market rule text; remote PDF bytes are not automatically pinned. Review must check applicable terms and source revisions.
4. Current Deribit instrument documentation's currency enum is narrower than some inverse products. Retain actual instrument currency. Never feed native coin option prices into a USD formula.
5. Actual real-world probability calibration, options-term extrapolation validity, microstructure robustness, and continuous live endurance remain unvalidated. No trading or profitability claim follows from the tests.

## Subsequent public smoke evidence

The five-second recorder check also captured an active BTC market (`kalshi_active_market.json`) and fixed-point book (`kalshi_book.json`), both via HTTP 200. The candidate `floor_strike` was 76392. These responses establish availability at their recorded receipt times, not resolution of the target-authority and rounding questions. All 15 generated forecast records remained unavailable; no authenticated feed was requested without credentials.
