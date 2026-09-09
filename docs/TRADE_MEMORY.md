# Trade memory, history and exports

The goal is understandable evidence for a person or an AI reviewer, without storing every derived paper-mode evaluation forever. Use [Recording](TRADE_RECORDING.md) for the exact full-versus-compact policy.

## What an opportunity record means

A retained evaluation includes its ID, run/mode/market, timestamp, configuration and version hashes, settlement specification, features, probabilities, quality, normalized book and reasons. **Paper mode only archives this evidence when a candidate first fills.** Prior to that it lives in the pending order checkpoint. A rejected live evaluation can appear in the UI without becoming an immutable `opportunity` row. Observation-only collection and BACKTEST retain full evaluation histories, including rejects/model failures.

Orders, cancellations, fills, exits and results reference their originating opportunity ID. An unfilled order can therefore have an ID without an exported opportunity: its pending evidence was discarded at cancellation. Inspect the order/cancellation and input tape, not a nonexistent replay packet. Later partial fills share the first-fill evidence; outcomes remain separate from predictions.

## Dashboard views

**Trade history** separates **Entry evidence**, **Orders / cancellations**, **Fills** and **Completed trades**. Select the correct mode and run, and use Refresh to load newer history. Entry-evidence counts are not all evaluated quotes in compact paper mode. A submitted order is not a fill; a buy fill is not necessarily a completed trade.

The normal scope is Settlement Edge. **Archived strategies (read-only)** exposes retained old evidence without executing its algorithms. Default headline results exclude archived portfolios and use the selected control configuration/run. Backtest totals can represent independent/repeated replays, not one live portfolio.

**View explanation** reads a retained opportunity and its market/run timeline. Some timeline events belong to other actions in the same market. Continuous reference/probability paths need full evaluations or recalculation from inputs; a paper packet containing one retained entry is not a full tick chart.

## Exports

Replace `OPPORTUNITY_ID` with a retained entry/evaluation ID and use its database:

```bash
uv run --locked btc15 export OPPORTUNITY_ID
```

Default output is `data/trade_packets/YYYY/MM/OPPORTUNITY_ID/`, selected by the export command's `--output` argument, not automatically relocated by changing `DATA_DIR`. Existing packet directories are refused, not overwritten.

Packets include summary/features/probability/decision/config/model versions, orders/fills, software snapshot, timeline, market/probability Parquet paths and `analysis.md`. The replay route is `/api/replay/OPPORTUNITY_ID`; JSON is available without scraping HTML. `/api/records`, `/api/trades`, `/api/runs` and `/api/analytics` accept the documented history scope. Read-only archived analytics is also available:

```bash
uv run --locked btc15 analytics --mode PAPER --run RETIRED_RUN_ID --archive
```

## Review limitations

Do not equate winning with sound reasoning or a large quote-check count with independent trades. Distinguish estimated fees/slippage at decision time from recorded simulated execution costs.

Calibration/rejection metrics are calculated from retained opportunities. Compact-paper data is selected by fills and is not a full-market calibration sample. Preserve and audit the complete tapes for rejected-opportunity/counterfactual analysis.

Current exports are not a completeness certification: some opportunity-path queries retain the Store default limit, and the software export selects the original run's source snapshot. After a code-changing resume, compare the selected entry's source hash and preserve the exact matching revision; do not assume a packet alone contains the right resumed software. Long-path completeness and code-changing-resume provenance require explicit review. These documentation cautions do not alter the exporter.

Never rewrite recorded predictions/fills when exploring an improvement. Produce a new replay run and keep its source/config/input identity. Credentials/private keys do not belong in packets or shared evidence.
