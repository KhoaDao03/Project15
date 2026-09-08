# Trade memory and exports

Each meaningful evaluation has a UUID, mode, run, market, complete effective
configuration, Git/source/config version hashes, model versions, settlement spec,
causal features, raw/conservative probabilities, uncertainty, model quality,
normalized book and structured rejection reasons. An unavailable model still
produces an opportunity containing the diagnostic failure. Rejected opportunities
are never discarded simply because no order was submitted.

Orders, partial fills, cancellations, exit intents and trade results reference
the originating opportunity. Market-level transitions and authoritative settlement
records are attached when constructing replay. Historical predictions remain
separate from outcomes. Full source and dependency-lock snapshots accompany runs,
including changes not yet committed to Git. Credentials are not part of snapshots.

`btc15 export <opportunity-id>` creates a new directory under
`data/trade_packets/YYYY/MM/<opportunity-id>/`. It contains summary, features,
probability, decision, orders, fills, config, versions, software snapshot, timeline,
market/probability Parquet paths and an analysis Markdown file. Existing packet
directories are not overwritten. Exports carry the recorded mode.

`/api/replay/<id>` returns opportunity, market timeline and probability/market path.
`/api/records` supplies paginated/filterable JSON. `/api/analytics` supplies scores,
counts, rejections, P&L paths and grouped research metrics. These APIs support an
AI analyst without scraping HTML. Analysis can propose experiments; no API can
change the running strategy or activate live orders.

Limitations: process-quality letter grading is not implemented; counts and grades
must not equate a winning outcome with sound reasoning. Replay charts do not yet
render all requested entry/exit/window overlays. Fee/slippage estimates and actual
simulated fees are distinct; they should not be silently substituted in analysis.
