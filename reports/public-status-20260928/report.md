# Intermittent public trading status — 2026-09-28

Reproduced read-only. The private fleet endpoint returns live.available=false when its executor status read exceeds the two-second timeout in execution_service.py. Four of 18 fleet samples returned unavailable; all 18 direct executor status reads completed with running=true, with a maximum latency of 2.255 seconds. Concurrent diagnostic reads add some load, so these timings are observations, not a calibrated production failure rate.

The actual public website uses public_export.py and the isolated public_site service, not the retired port-8001 dashboard. Exported view.json independently flipped live_available to false with fresh timestamps and missing live policies for all ten markets. This propagates the status timeout into every public trading badge. It was not the twenty-second snapshot-age limit in these observations. Executor, exporter and public website were running; no automatic executor or public-site restarts were reported. Successful status reads do not establish that every market was eligible to trade.

Recommended fix: publish a lightweight timestamped status/heartbeat independently of history work; retain last confirmed policy with an explicit delayed/unknown freshness state instead of erasing all policies after one timeout. Keep actual order authorization and freshness checks unchanged. Coordinate timeout budgets across the executor reader and public exporter; increasing only the two-second timeout can hit the exporter’s three-second timeout instead. The underlying source of executor latency requires further profiling.

No application changes, restarts, trading actions or logging-control changes were made. Probe evidence is adjacent to this report.
