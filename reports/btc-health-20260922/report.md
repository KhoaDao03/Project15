# BTC automatic-entry health block — 22 September 2026

BTC is intermittently accumulating an input-processing backlog. The live executor's captured rejection traces explicitly report `COLLECTOR_RECOVERING` and `BACKLOG_WARNING`, with the collector reporting `BACKLOG_NOT_DRAINED`. One rejection used status only 5 ms old and processing lag of 565 ms, so these examples are fresh health blocks, not merely a stale dashboard label.

During a 60-second read-only observation near 06:28–06:29 UTC, sampled every 0.5 seconds:

| Metric | BTC | ETH |
| --- | ---: | ---: |
| Blocked health samples | 36 / 120 | 1 / 120 |
| Median processing lag | 202 ms | 3.5 ms |
| p95 processing lag | 1.89 s | 235 ms |
| Maximum processing lag | 2.17 s | 559 ms |
| Maximum sampled queue | 4,431 | 461 |

All 36 blocked BTC samples included recovery and backlog warnings; 13 additionally exceeded the executor's one-second processing-lag limit and four reported stale reference data. These are health observations, not counts of missed trades. Recording-drop counters did not increase during this sample.

The collector raises a backlog warning at 0.5 seconds and requires the backlog to drain below its recovery thresholds before entry resumes. The executor rejects both recovery and warning states. Therefore lag below its separate one-second hard limit can still block entry. Initial inspection found an active connection, valid clock, open exchange and enabled BTC policy at 10 contracts. The services had no automatic restarts. A later entry evaluation also failed MIN_PRICE and MIN_PROBABILITY: clearing health alone does not imply a trade.

The running collector's archived engine.py, runner.py and research_log.py exactly match current source, including the earlier quote-check optimization and prompt recovery-status publication fix. Further restarts would not load a newer version of those files. Prior throughput work reduced redundant checks but did not eliminate burst-related BTC pauses.

The exact rejection cause is established. A fresh CPU profile is still needed to apportion the remaining cost among book processing, quote checks, reference calculations and recording/serialization. Existing source executes raw input and processing capture for every event, with a recorder thread sharing the process. Reduce measured hot-path cost while retaining full capture, and retest during bursts; do not raise freshness limits simply to suppress the message. The dashboard should expose the actual recovery reason, lag and queue depth rather than only the generic rejection.

Evidence: health-samples.json, summary.json and executor-rejections.json in this directory. No service restart, configuration change, trade or production-code edit was performed.
