# Silver loss review — September 23, 2026

## Scope and method

Read-only review of local `data/cloud/manual-orders.sqlite`, silver collector settlement records across configuration migrations, and finalized silver research segments. No strategy settings, services or live controls were changed. Existing unrelated working-tree changes were left intact.

41 filled bot purchases, all 10 contracts, were reconstructed from cumulative confirmed exchange order economics. Partial sell orders are aggregated. Sale proceeds use quantity minus the exchange's opposite-outcome fill cost, not the acknowledgement's potentially misleading average price. Fees are deducted on both legs. Remaining inventory uses the recorded official result; duplicate migrated settlements are checked for conflicting outcomes. Dashboard-cleared trades are included. No mixed manual buys or conflicting sides were encountered. All 41 trades have closed accounting.

This is the available local history, not a guarantee of a complete exchange-account export. Entries range from September 18 07:44 UTC through September 23 05:14 UTC. Configurations changed during this period. Current local manifest/config and recent decision records use a 420-second start, 84% confidence floor, 97-cent ask ceiling and effectively disabled spread ceiling. Recent recorded sigma multipliers change from 1.35 to 1.20. Historical results must not be attributed to a single current strategy.

## Actual results

| Metric | Result |
|---|---:|
| Completed trades | 41 |
| Profitable / losing trades | 33 / 8 |
| Profitable-trade percentage | 80.5% |
| Gross P&L | -$22.63784 |
| Fees | $3.06056 |
| Net P&L | **-$25.69840** |
| Total gains | $14.81530 |
| Total losses | -$40.51370 |
| Average gain | $0.44895 |
| Average loss | -$5.06421 |

One average loss consumes about 11.3 average wins. At these fixed average payoffs, the break-even profitable-trade rate would be approximately 91.9%; this is descriptive arithmetic, not a forecast or a suggested model probability threshold.

| Entry day, UTC | Trades | Wins | Losses | Net P&L |
|---|---:|---:|---:|---:|
| September 18 | 9 | 7 | 2 | -$5.8435 |
| September 19 | 5 | 3 | 2 | -$6.3509 |
| September 21 | 7 | 6 | 1 | -$3.0927 |
| September 22 | 13 | 13 | 0 | +$4.1460 |
| September 23 | 7 | 4 | 3 | -$14.5573 |

## Every loss

All eight losses exited through the hard stop. Prices below are quantity-weighted actual fills.

| Market suffix after KXSILVER15M- | Entry UTC | Buy | Sell | Net loss | Bought side eventually won? |
|---|---|---:|---:|---:|---|
| 26SEP180415-15 | Sep 18 08:14:28 | 95.00c | 32.00c | -$6.4857 | No |
| 26SEP181800-00 | Sep 18 21:58:19 | 94.235c | 55.00c | -$4.1349 | No |
| 26SEP182115-15 | Sep 19 01:08:56 | 91.60c | 47.003c | -$4.6880 | No |
| 26SEP182130-30 | Sep 19 01:25:05 | 87.591c | 53.00c | -$3.7096 | Yes |
| 26SEP210130-30 | Sep 21 05:26:03 | 95.98c | 46.00c | -$5.1990 | No |
| 26SEP222045-45 | Sep 23 00:44:12 | 88.00c | 48.00c | -$4.2488 | Yes |
| 26SEP230000-00 | Sep 23 03:55:05 | 94.60c | 30.00c | -$6.6428 | Yes |
| 26SEP230115-15 | Sep 23 05:14:44 | 95.00c | 43.00c | -$5.4049 | No |

Holding the same 41 purchases entirely to settlement, deducting recorded entry fees and eliminating actual sale fees, yields **-$26.4074**, compared with actual -$25.6984. This fixed-purchase counterfactual ignores effects on subsequent capital and guard decisions. Removing all stops/exits is not supported by this sample.

## Recent loss traces

### Sep 23 00:44 — 88c purchase, 48c exit

At the initial accepted entry check, model confidence was 84.40%, ask 97c and spread 0.8c. During preflight the book moved; the purchase actually filled at 88c. The decision's estimated net EV was -13.01c per contract at its quoted price. A lower realized fill than the initial quote does not itself establish favorable execution: prices were moving sharply.

The retained book showed an 80c bid at about 00:44:31.928, then 55c at 00:44:32.477. Resting-offer cancellation was requested at 00:44:33.744, attempted at 00:44:34.987, and reconciled by 00:44:35.338. The hard-stop order was requested at 00:44:35.944 and submitted approximately 0.614 seconds later. The actual exit averaged 48c. The side eventually won settlement.

This supports investigating cancellation/scheduling latency in addition to price jumps. It does not prove that an earlier order would have filled at 55c.

### Sep 23 03:55 — 94.6c purchase, 30c exit

Entry confidence was 84.15%, quoted ask 94.4c and spread only 0.1c. Recorded top bid quantity was over 100 contracts. A simple two-cent spread ceiling or a ten-contract top-depth check would not have rejected this trade.

Near the exit, retained bids moved from 63c at approximately 03:58:42.902, to 55c at 03:58:44.134, 42c at 03:58:44.709, then 29c at 03:58:45.685. The stop request was 03:58:44.854 and submission approximately 0.615 seconds later; the actual exit was 30c. The resting offer had already been canceled at roughly 03:57:33, so resting cancellation was not the immediate bottleneck here. The position ultimately would have won settlement.

### Sep 23 05:14 — 95c purchase, 43c exit

Entry confidence was 85.94%, with roughly 15.5 seconds remaining and a 4.2c initial spread. The recheck spread was still 2.5c. The bid was about 95.2c at 05:14:53.677, 69c at 05:14:54.682, and around 40c by 05:14:55.232. Resting cancellation and stop submission occurred during this collapse. The purchase-to-stop-request interval was approximately 11.2 seconds. The bought side lost settlement.

Avoiding entries in the final 30 seconds or requiring a two-cent spread limit would have rejected the observed entry attempt. A replacement attempt at another time is not simulated.

## Entry-filter sensitivity

Finalized executor logs preserve usable accepted entry checks for 18 trades, consisting of 15 wins and three losses, totaling -$11.1636. The following table keeps or removes each actual trade according to its nearest accepted live check to the buy request. It does not simulate delayed/replacement entries, changed fills, portfolio feedback, loss-guard state or full intramarket replay. Therefore positive values are diagnostic, in-sample outcomes, not demonstrated strategy performance.

| Filter applied to observed entries | Kept | Wins / losses | Net of kept actual trades |
|---|---:|---|---:|
| None | 18 | 15 / 3 | -$11.1636 |
| Recorded model net EV >= 0 | 1 | 1 / 0 | +$0.4025 |
| Confidence >=85% | 6 | 5 / 1 | -$3.7035 |
| Confidence >=86% | 3 | 3 / 0 | +$1.2113 |
| Confidence >=88% | 2 | 2 / 0 | +$0.6569 |
| Spread <=2c | 14 | 12 / 2 | -$6.6513 |
| More than 30 seconds remaining | 14 | 12 / 2 | -$6.7232 |
| More than 60 seconds remaining | 10 | 9 / 1 | -$3.6875 |
| Confidence >=85% AND spread <=2c | 3 | 3 / 0 | +$1.0445 |
| Confidence >=85% AND more than 30 seconds remaining | 4 | 4 / 0 | +$1.2989 |

Seventeen of these eighteen entries had negative reported model EV; fourteen of those seventeen were actual profitable trades. This exposes a mismatch between model valuation and the trading policy, but does not validate the model's calibration. Enforcing positive model EV is economically coherent if that estimate is trusted, yet would almost eliminate this observed activity. It is not proven to be the best way to retain profitable turnover.

Increasing confidence alone to 85% did not solve losses. The combinations above are candidates for independent evaluation, not tuned production recommendations: only three or four kept trades is far too little evidence.

## Stop sensitivity and execution

Sampled valid/fresh books exist for the same 18 markets. An illustrative earlier-stop calculation uses the first retained trigger after buy acknowledgement and before the first actual sale, then the first later retained depth snapshot after an assumed 0.7-second response. It consumes recorded top-five bid depth and estimates taker fees. Untriggered trades retain their actual result. This omits cancellation sequencing, queue priority, intervening quotes, missing records and behavioral feedback.

- A 65c trigger produces approximately -$7.14 versus the -$11.16 baseline.
- A 70c trigger produces approximately -$8.02.
- A 75c trigger produces approximately -$7.61.
- The 65c illustration turns one +$0.40 winner into an approximately -$0.24 loss while reducing the three existing losses.
- Two-second response assumptions lack timely retained post-trigger books for some trades; no complete total is reported for those cases.

These are not executable backtests. They illustrate why earlier stops can reduce tail losses yet still leave the strategy losing money. Seven of eight actual losing trades sold below 55c. All eight recorded stop submissions spent about 0.59–0.60 seconds in preflight, with REST request network times often much smaller than read-rate waiting. Reduce redundant reads or prioritize urgent exits only while preserving authoritative holdings checks, cancellation reconciliation and duplicate-sale protection.

Historical resting-take-profit failures are not the leading current explanation. Sixteen trades have a rejected resting placement, but recent trades show successful working/canceled resting orders and real resting profit fills. Current order semantics distinguish immediate-or-cancel and good-till-canceled orders: https://docs.kalshi.com/api-reference/orders/create-order-v2 .

## Recommended order of work

1. **Evaluate a selective silver entry policy in shadow:** restore at least the old 85% floor and separately test a two-cent spread limit and a 30-second final-entry cutoff. Compare each change and their combinations against an unchanged baseline on later unseen markets. Record rejected opportunities and whether they later become eligible; simply deleting historical losing entries overstates benefits.
2. **Make the permitted purchase price responsive to value and the current quote.** The current checked-in live ceiling is the configured 97c maximum while the model accepts confidence around 84%; earlier execution versions allowed an extra cent. Test a price/value margin and limit slippage relative to the contemporaneous ask, rechecking at submission. Do not rely on setting a paper EV flag without confirming the live submitted-limit path enforces it. Treat the existing model as uncalibrated; report both opportunity loss and drawdown.
3. **Prioritize stop handling and reconcile cancellations promptly.** Investigate the measured cancellation-to-submission and preflight delays before changing the stop to 65–70c. Preserve order-state and holdings safeguards. Faster execution may help; jumps can bypass any trigger.
4. **Do not remove stops, loosen stops, or further lower the ATR multiplier based on this sample.** Lower sigma makes distance-based confidence higher, which can admit more marginal entries; it has not demonstrated a silver P&L improvement here. The recent 1.20 period is too short and confounded for a causal comparison.
5. **Limit dollar exposure during evaluation.** Smaller size reduces exposure but does not cure negative expected returns. Keep it separate from strategy-quality claims.

The available evidence identifies promising tests and specific execution delays, but does not establish a profitable parameter set. The first acceptance criterion should be better net P&L and drawdown on subsequent data, with actual costs and rejected winners counted.

## Coverage and reproducibility

Finalized retained research segments cover roughly September 22 18:52 UTC through September 23 05:53 UTC, with different bounds by producer. Earlier sessions often have manifests but no retained finalized segments. Four relevant collector/executor session status files all declare incomplete capture and OperationalError, with dropped-record counts of 788, 696, 169 and 178 at inspection. Sampled books cannot substitute for complete depth replay. These research capture errors do not by themselves prove trading used stale data.

Artifacts: `trades.json` contains confirmed accounting, `entry-checks.json` contains supporting live checks, `entry-comparison.json` contains fixed-entry filter outcomes, `book-paths.json` contains retained sampled books, and `stop-sensitivity.json` contains the deliberately limited stop experiment. `evaluation-paths.json` is empty because those summaries did not provide a market field usable by that extractor; no finding relies on it.

Run with Python 3 from any directory:

```sh
python3 /root/Project15/reports/silver-loss-review-20260923/analyze.py
python3 /root/Project15/reports/silver-loss-review-20260923/compare.py
python3 /root/Project15/reports/silver-loss-review-20260923/paths.py
python3 /root/Project15/reports/silver-loss-review-20260923/stop_sensitivity.py
```

Source databases are opened read-only in transactions. Multiple databases are not one atomic snapshot; results reflect the local snapshots available during the audit. Re-running against updated journals or expired research segments can change counts or coverage.
