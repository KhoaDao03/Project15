# ATR test review — 30 September 2026

## Recommendation

**Test modest reductions in shadow mode first. The logs support investigating cheaper entries, but do not establish an optimal ATR or a profitable lower-ATR strategy. No bot settings, orders, services, or live-buy permissions were changed.**

| Asset | Running ATR | First test | Second test, if first improves net results | Early minimum |
|---|---:|---:|---:|---:|
| XRP | 1.25 | 1.10 | 1.00 | 85% |
| ETH | 0.95 | 0.90 | 0.85 | 83% |
| DOGE | 1.10 | 1.00 | 0.95 | 83% |
| BNB | 1.25 | 1.15 | 1.10 | 91% |
| HYPE | 1.15 | 1.10 | 1.05 | 87% |

Keep the current ATR as the control. Keep probabilities, the 10-minute entry window, contract quantity, price bounds, and stop rules unchanged during the comparison. The early threshold applies between 10 and 7 minutes remaining; the threshold becomes 83% at 7 minutes. HYPE’s verified running ATR is 1.15. These proposed test levels are cautious engineering choices, not statistically estimated optima.

## What the actual dashboard trades show

Snapshot taken around 18:48 UTC on 30 September, covering closed dashboard-visible trades through the 18:45 UTC market close. ETH/XRP history starts 24 September; BNB/HYPE/DOGE starts 26 September. Earlier cleared dashboard history is outside this sample. Results span several ATR, entry-window, probability, sizing, and spread-filter regimes. They must not be attributed solely to the current settings.

All 1,048 closed trade groups were reconciled to the execution journal for purchased quantity and purchase cost. Fees and realized proceeds use the dashboard’s actual execution/settlement accounting. All losing trades in this sample were recorded as stops.

Each entry and per-contract return below is averaged across trades after dividing by that trade’s purchased contracts; actual dollar P&L retains real quantities. This avoids giving XRP’s larger trades disproportionate weight in the margin comparison.

| Asset | Trades | Realized win rate | Mean entry | Mean net winner / contract | Mean net loser / contract | Actual net P&L | Break-even win rate* |
|---|---:|---:|---:|---:|---:|---:|---:|
| XRP | 233 | 88.4% | 92.13¢ | +7.04¢ | -40.02¢ | $+38.22 | 85.0% |
| ETH | 347 | 85.0% | 92.25¢ | +7.04¢ | -41.86¢ | $-10.03 | 85.6% |
| DOGE | 115 | 91.3% | 93.10¢ | +6.33¢ | -42.34¢ | $+24.12 | 87.0% |
| BNB | 176 | 85.8% | 92.66¢ | +6.49¢ | -40.66¢ | $-3.58 | 86.2% |
| HYPE | 177 | 83.1% | 93.11¢ | +6.34¢ | -38.31¢ | $-21.68 | 85.8% |

*Break-even = average loss ÷ (average win + average loss), using the observed normalized payoff sizes. It is descriptive, not a future requirement guaranteed to remain constant.

Your small-margin concern is supported: the mean entry is roughly 92–93¢ and one average stop erases roughly six average winners. However, win rate and margin cannot be separated: ETH, BNB, and HYPE’s observed win rates are below the break-even rates implied by their actual payoffs. Lower ATR needs to improve net return after any additional stops, not just increase the size of winning trades.

The approximate 95% Wilson intervals for trade win rate are: XRP 83.7%–91.9%; ETH 80.9%–88.4%; DOGE 84.7%–95.2%; BNB 79.9%–90.2%; HYPE 76.8%–87.9%. These intervals do not account for serial dependence; related crypto trades are not independent experiments.

### Current spread-disabled period

Since 30 September 07:33 UTC, sample sizes are much smaller. This is the closest realized cohort to the running filter policy; it is too short to select an optimal ATR.

| Asset | Trades | Wins | Net P&L |
|---|---:|---:|---:|
| XRP | 9 | 8 | $+1.92 |
| ETH | 22 | 18 | $-5.63 |
| DOGE | 10 | 10 | $+9.34 |
| BNB | 5 | 5 | $+2.88 |
| HYPE | 3 | 2 | $-3.83 |

### Actual cheaper entries are not uniformly better

Each cell is number of trades / realized win rate / average net cents per contract, including losing trades and fees. Price groups are descriptive and confounded by market conditions and prior settings; they are not randomized ATR experiments.

| Asset | 80–<85¢ | 85–<90¢ | 90–<94¢ | 94–<97¢ |
|---|---|---|---|---|
| XRP | 18 / 72% / +2.43¢ | 25 / 80% / +1.98¢ | 81 / 91% / +3.31¢ | 109 / 91% / +0.09¢ |
| ETH | 11 / 64% / -1.85¢ | 42 / 76% / +0.12¢ | 166 / 85% / -0.24¢ | 127 / 90% / -0.55¢ |
| DOGE | 9 / 78% / +4.95¢ | 5 / 100% / +12.34¢ | 33 / 91% / +2.35¢ | 68 / 93% / +0.85¢ |
| BNB | 9 / 33% / -17.52¢ | 20 / 75% / +0.72¢ | 56 / 93% / +3.95¢ | 91 / 89% / -1.25¢ |
| HYPE | 7 / 71% / +3.38¢ | 6 / 67% / -3.97¢ | 79 / 86% / +1.24¢ | 85 / 82% / -3.70¢ |

The price bins omit any out-of-band fills and therefore need not sum to the full history. In particular, one historical ETH trade filled at 74.9¢; it is outside these price bins and should not be treated as evidence for the current 80–96¢ entry policy.

XRP and DOGE justify investigating modest reductions. BNB deserves particular caution: its nine sub-85¢ entries averaged a loss of 17.52¢ per contract. HYPE’s 94¢+ group was weak, but its cheaper groups are small and inconsistent. ETH’s cheaper groups do not establish a robust positive edge.

Of the stopped trades, the original selected side eventually settled correctly in ETH 24/52, XRP 16/27, DOGE 5/10, BNB 17/25, and HYPE 16/30. This does **not** recommend removing stops: it shows why a settlement-only backtest would overstate this strategy’s realized win rate. Earlier entry also means more time exposed to an interim stop.

## Previous ATR regimes

These are actual trades assigned to the most recent collector manifest at entry time. Trades before the first retained collector manifest are excluded. Most displayed rows use the 10-minute window; HYPE 0.85 is included separately because it illustrates the risk of a large reduction. Several other parameters and market conditions changed, so this is not a causal ATR comparison.

| Asset | ATR | Early minimum | Window | Trades | Wins | Net P&L | Mean entry |
|---|---:|---:|---:|---:|---:|---:|---:|
| XRP | 1.25 | 85% | 10 min | 16 | 15 | $+8.08 | 92.83¢ |
| XRP | 1.10 | 85% | 10 min | 35 | 30 | $-0.72 | 93.22¢ |
| ETH | 0.95 | 83% | 10 min | 44 | 35 | $-18.17 | 91.71¢ |
| ETH | 0.80 | 90% | 10 min | 79 | 69 | $+9.41 | 92.01¢ |
| DOGE | 1.10 | 83% | 10 min | 57 | 52 | $+14.39 | 92.72¢ |
| BNB | 1.25 | 91% | 10 min | 13 | 12 | $+1.00 | 94.13¢ |
| BNB | 1.00 | 86% | 10 min | 47 | 37 | $-12.32 | 92.06¢ |
| HYPE | 1.15 | 87% | 10 min | 8 | 7 | $+0.38 | 92.20¢ |
| HYPE | 1.00 | 87% | 10 min | 7 | 4 | $-8.84 | 91.92¢ |
| HYPE | 1.05 | 84% | 10 min | 34 | 30 | $+3.39 | 93.16¢ |
| HYPE | 1.00 | 84% | 10 min | 9 | 7 | $-4.48 | 93.36¢ |
| HYPE | 0.85 | No early tier | 7 min | 22 | 13 | $-22.22 | 91.83¢ |

The actual history does not establish that lower ATR is better. HYPE’s 0.85 period lost $22.22 over 22 trades; BNB’s 1.00 / 86% / 10-minute period lost $12.32 over 47 trades. Even XRP’s 1.10 / 85% / 10-minute period was slightly negative. These observations strengthen the case for controlled shadow tests rather than adopting a lower ATR directly.

## Why lower ATR can help, and where it cannot

The bot scales uncertainty as `reference ATR × sqrt(seconds remaining / 60) × ATR multiplier`. Reducing the multiplier makes the model more confident for the same price distance and remaining time. It can cross the entry threshold sooner. It does not change the exchange price or prove the true probability of winning increased.

For example, changing XRP from 1.25 to 1.10 reduces modeled uncertainty by 12%, and increases its distance-to-uncertainty ratio by about 13.6%. Whether that produces a cheaper fill depends on the actual book and all the other gates.

The market-confidence cap and the 80¢ minimum ask still apply:

| Asset | Early threshold | Midpoint + cap allowance | Minimum midpoint implied by cap |
|---|---:|---:|---:|
| XRP | 85% | +10 percentage points | 75¢ |
| ETH | 83% | +6 points | 77¢ |
| DOGE | 83% | +6 points | 77¢ |
| BNB | 91% | +6 points | 85¢ |
| HYPE | 87% | +6 points | 81¢ |

These are midpoint floors, not exact ask floors: the ask is normally above the midpoint. At 7 minutes all five use 83%, giving a 77¢ midpoint floor for ETH/DOGE/BNB/HYPE and 73¢ for XRP, alongside the unchanged 80¢ ask gate. BNB’s 91% early threshold therefore limits the cheap-entry benefit of lowering ATR.

A winning settlement’s gross headroom is $1 minus entry price, before fees. Lower entry prices can improve that headroom, but fees and losing exits must be included. This report uses recorded fees rather than substituting a generic estimate. Kalshi confirms that transactions carry fees: [official fee explanation](https://help.kalshi.com/en/articles/13823767-how-does-kalshi-make-money).

The live bot submits a full-fill-or-kill order with a 96¢ limit. An earlier 85¢ displayed ask is not a promise of an 85¢ fill for the whole quantity; depth, intervening updates, and execution latency matter.

## Recorded-decision sensitivity study

The study reconstructs the model from recorded safety ratio, multiplier, indicator lean/weight, and settlement direction. Every retained record is checked against its original model probability before counterfactual use. It applies today’s entry windows, probability thresholds, price bounds and confidence caps, while retaining the historical selected side and all other recorded rejection gates. Historical SPREAD and ENTRY_WINDOW rejections are replaced by today’s policy; MIN_PROBABILITY is recomputed. It keeps the first otherwise-eligible record per market per wall-clock second.

For each multiplier, the study selects the first passing **recorded** opportunity in each market. Pairwise savings compare current ATR versus candidate ATR only where both have a recorded entry on the same side. Positive savings mean a cheaper displayed ask. These are opportunity measurements, not simulated fills or P&L. New opportunities have no paired baseline entry.

| Asset | Valid snapshots | Eligible markets | Snapshots blocked by confidence cap |
|---|---:|---:|---:|
| XRP | 17,210 | 141 | 0 (0.0%) |
| ETH | 18,598 | 148 | 6 (0.0%) |
| DOGE | 20,077 | 153 | 13 (0.1%) |
| BNB | 18,588 | 142 | 2,257 (12.1%) |
| HYPE | 17,883 | 142 | 776 (4.3%) |

Full candidate comparison:

| Asset | ATR | Markets with recorded entry | Added vs current ATR | Paired same-side markets | Cheaper / dearer pairs | Mean paired ask saving | Mean paired seconds earlier |
|---|---:|---:|---:|---:|---:|---:|---:|
| ETH | 0.95 | 81 | 0 | 81 | 0 / 0 | +0.00¢ | 0.0 |
| ETH | 0.90 | 97 | 16 | 80 | 28 / 2 | +0.50¢ | 6.6 |
| ETH | 0.85 | 111 | 30 | 79 | 39 / 4 | +1.17¢ | 16.0 |
| ETH | 0.80 | 116 | 35 | 79 | 46 / 5 | +2.11¢ | 29.2 |
| ETH | 0.75 | 124 | 43 | 78 | 49 / 2 | +2.69¢ | 39.9 |
| XRP | 1.25 | 27 | 0 | 27 | 0 / 0 | +0.00¢ | 0.0 |
| XRP | 1.15 | 37 | 10 | 27 | 15 / 2 | +0.84¢ | 15.2 |
| XRP | 1.10 | 45 | 18 | 27 | 18 / 2 | +1.23¢ | 22.1 |
| XRP | 1.00 | 71 | 44 | 27 | 22 / 1 | +2.12¢ | 41.2 |
| XRP | 0.90 | 99 | 72 | 27 | 23 / 0 | +3.17¢ | 72.0 |
| DOGE | 1.10 | 56 | 0 | 56 | 0 / 0 | +0.00¢ | 0.0 |
| DOGE | 1.05 | 64 | 8 | 56 | 20 / 6 | +0.36¢ | 10.0 |
| DOGE | 1.00 | 71 | 15 | 56 | 27 / 5 | +0.72¢ | 19.1 |
| DOGE | 0.95 | 81 | 25 | 56 | 37 / 3 | +1.30¢ | 29.6 |
| DOGE | 0.90 | 95 | 39 | 56 | 41 / 2 | +1.96¢ | 38.5 |
| BNB | 1.25 | 34 | 0 | 34 | 0 / 0 | +0.00¢ | 0.0 |
| BNB | 1.15 | 43 | 9 | 33 | 13 / 3 | +0.45¢ | 9.3 |
| BNB | 1.10 | 50 | 16 | 33 | 15 / 3 | +0.68¢ | 24.6 |
| BNB | 1.00 | 70 | 36 | 32 | 17 / 6 | +1.46¢ | 50.4 |
| BNB | 0.90 | 83 | 49 | 30 | 20 / 4 | +2.01¢ | 72.9 |
| HYPE | 1.15 | 38 | 0 | 38 | 0 / 0 | +0.00¢ | 0.0 |
| HYPE | 1.10 | 50 | 12 | 38 | 12 / 3 | +0.35¢ | 9.3 |
| HYPE | 1.05 | 64 | 26 | 37 | 19 / 2 | +0.70¢ | 17.4 |
| HYPE | 1.00 | 76 | 38 | 37 | 24 / 2 | +1.31¢ | 21.6 |
| HYPE | 0.90 | 98 | 60 | 37 | 28 / 3 | +2.49¢ | 44.9 |

### Interpretation of the sensitivity results

- **XRP 1.10:** mean paired ask saving 1.23¢ across 27 same-side paired markets, with 18 cheaper and two dearer; 18 additional markets passed. It offers the largest first-test price improvement among the proposed reductions, but added entries still averaged 94.87¢.
- **ETH 0.90:** mean paired saving 0.50¢ across 80 paired markets; 16 additional markets passed. A restrained first step. The 0.85 alternative showed 1.17¢ savings but admitted more new opportunities.
- **DOGE 1.00:** mean paired saving 0.72¢ across 56 paired markets; 15 additional markets passed. Five paired opportunities were actually dearer. This is a modest benefit to test against an already profitable historical baseline.
- **BNB 1.15:** mean paired saving only 0.45¢ across 33 paired markets. The nine added opportunities averaged 94.92¢, so the average first ask across all admitted markets increased from 92.19¢ to 92.51¢. Lower ATR can increase trading without improving overall purchase price. Lowest-priority reduction.
- **HYPE 1.10:** mean paired saving 0.35¢ across 38 paired markets; 12 additional markets passed. Use the smaller first step because historical net performance is weak. At 1.05, savings rose to 0.70¢ but 26 additional markets passed versus only 38 at the baseline. That expansion needs stop-aware validation.

**ATR alone looks like a modest margin lever, not a demonstrated solution to the loss/win imbalance.** Typical first-test savings are below one cent, except XRP. Reductions can also change which side is entered first; those opposite-side pairs are excluded from paired savings but remain in the all-opportunity counts. No profitability claim is made for added markets.

### Coverage limits

Scanned 443,828 evaluation records from 29 collector sessions, across complete closed gzip segments retained from 26–30 September. Active `.part` segments were excluded. This is the retained research-log scope, distinct from the longer realized-trade history.

**The current research recordings are incomplete.** Collector status reports substantial queue-overflow drops, and the most recent closed segments contain observations near 07:33–07:34 UTC despite being written much later. Capture time, not file write time, was used. Full status and manifests are saved in `all-capture-summary.json`. The recording problem alone does not establish that the bots’ live input feeds were stale.

The operational rejection summaries remain available and show repeated MIN_PROBABILITY, MIN_PRICE and MAX_PRICE blocks across the current markets. Those counts overlap and include repeated checks, so they cannot be interpreted as independent missed trades or ATR-only failures. See `operation-rejections.json`.

Consequently, this is **not an execution-equivalent backtest**. It cannot establish the true earliest entry, fillability, all stop crossings, counterfactual realized P&L, or future profitability. The records also span changing ATR/probability regimes, ATR floor fallback, missing data and market conditions. Observed savings are conditional on surviving records. There is no untouched out-of-sample validation set in this report.

## How to run the tests

1. Restore reliable decision/book recording before collecting the forward comparison; verify drop counts and coverage. Do not treat sparse recordings as complete market paths.
2. Run the current ATR and the proposed first-test ATR in parallel shadow evaluation on the same incoming markets. Keep all other settings and quantity fixed. Do not increase size to compensate for small margins.
3. Record first eligible time, side, executable ask/depth for the full quantity, fees, realistic latency/slippage, stop path, and final realized-equivalent result. Include all eligible markets, rejected/unfilled attempts and adverse outcomes.
4. Compare net return per contract across all trades, total net P&L, stop frequency, loss size, drawdown, and entry prices. Winning margin alone is insufficient. Track added trades separately from entries shared with the control.
5. Make a first review after at least seven days and 100 completed candidate trades per asset; that is a practical review point, not statistical proof. With few losses and correlated markets, several hundred observations or longer may be necessary. Reserve later markets as an untouched chronological validation period. Only test the second reduction if the first improves net results without disproportionate stop losses.

XRP and DOGE are the cleaner first candidates. ETH and HYPE warrant smaller, closely monitored experiments. Keep BNB’s first reduction modest; its early probability gate and poor small sample of cheap fills argue against a large immediate cut.

## Reproducible artifacts

- `trades.csv`, `trade-summary.json`, and the five `*-ui-history.json` files: actual dashboard outcomes and margins.
- `active-parameters.json`, `current-evaluations.json`: running parameter verification.
- `sensitivity.csv`, `first-observed-opportunities.csv`, `sensitivity-quality.json`: counterfactual opportunity statistics and reconstruction checks.
- `all-capture-summary.json`, `operation-rejections.json`: recording coverage and operational rejection evidence.
- `extract_eligible.py`, `sensitivity.py`, `trades.py`, `report.py`: analysis scripts. The extract is stored as `eligible.jsonl.gz` after processing to conserve space.

Production strategy, configuration and services were left unchanged.
