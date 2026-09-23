# Bot reliability audit — 21 September 2026

**The bots are operating, but this audit found remaining bottlenecks. It does not establish interruption-free 24/7 operation.** No production settings, orders, services or code were changed during this audit.

Observation: 01:40:59–01:48:58 UTC (8.0 minutes), including market rollover. The 73 probes were sequential local reads; they sample conditions, not every event or trading opportunity. Profiling and tests add some audit load.

| Check | Result |
| --- | --- |
| BTC health samples | 73 successful, 0 failed probes |
| BTC collector entry gate blocked | 6 sampled observations |
| BTC processing lag | Median 3.3 ms; sampled p95 496.8 ms; maximum 1.70 s |
| Live-executor heartbeat age | Reached 3.81 s; this is not order execution latency |
| New BTC research-record drops during observation | 0 |
| Services | Seven collectors, executor and dashboards running; no failed user units |
| Capacity | 6 vCPUs; approximately 4.3 GiB available RAM and 207 GiB free disk |
| Restart configuration | Enabled at boot; user lingering enabled; restart on failure after 15 seconds |
| Offline safeguards | 77 tests passed; 1 JavaScript test skipped because Node.js is unavailable |

## Findings and priorities

1. **Shared executor CPU is the first performance priority.** It uses approximately one full CPU core. A ten-second profile collected 728 CPU-owning samples; 483 included the daily-loss history-loading line. Every check loads and decodes the whole order journal before filtering for the asset. The journal already contains 1,904 order records, while 1,263 historical market controls remain. This repeated work grows with history. Use targeted indexed reads while preserving the exact daily-loss calculation, carry-over positions and rearm baselines. Profile again afterward.

2. **BTC still has real burst-related entry pauses.** The accounting correction is loaded, and the bot recovers, but 6 sampled health checks were blocked. These observations do not establish how many trades were missed. The earlier short clean interval was not proof of uninterrupted operation. Continue throughput work using sustained high-volume and rollover checks; retain freshness and loss safeguards.

3. **Recording reliability needs further work.** BTC had 3,510 previously marked queue-capacity drops in its current session, with no additional drops during this observation. Other collectors have recorded OperationalError capture failures. The manual-order database uses SQLite DELETE journaling with multiple readers/writers; contention is a plausible contributor, not proven by the current class-only error messages. Read-only audit queries succeeded during inspection. Capture useful exception details, identify the failing operation, then test a concurrency fix. Existing data-gap markers must remain truthful.

4. **Crash restart does not cover a hung process.** Systemd restart-on-failure is configured, and in-process feed recovery exists. There is no systemd watchdog or project health timer observed. Add independent heartbeat monitoring and order-aware recovery, especially for the shared live executor.

5. **Seven days of local recordings will not fit the present cap at the observed rate.** A short 105-second sample measured about 1.49 GB/hour of research-log growth. At that rate, a 20 GiB cap holds about 14.4 hours, not seven days. Growth varies with activity. The retention cap protects disk space by removing older closed segments; arrange export or adjust the recording/storage budget for longer analysis coverage. This short extrapolation is not a storage guarantee.

6. **The newest accounting/logging changes are loaded only in BTC.** Source snapshots confirm all seven have the catch-up recovery logic, but the other six still have earlier runner and recorder versions. Include them in a coordinated safe rollout when the next performance change is deployed.

## Execution and practical limits

Recent exchange 404 responses did not leave the examined BTC orders unresolved: the 10-contract buy and its 10-contract take-profit sale were both subsequently confirmed executed. No unresolved journal order older than an hour was found at inspection. The example buy took about 1.35 seconds from decision timestamp to submission and 2.78 seconds to fill confirmation; its preflight included rate-limit waiting. These are individual observations, not latency percentiles.

The existing service processes remained unchanged during the audit. No production fault injection, reboot, forced disconnect or position interruption was performed. A short soak and passing tests cannot establish behavior over days, exchange outages, every market burst or indefinite history growth.

**Recommended next work:** optimize and validate daily-loss history reads first; address recording errors and safely deploy the latest collector changes; add independent health supervision; then run a longer soak with explicit thresholds for processing lag, executor responsiveness, recording drops, memory growth and recovery time.

Raw evidence is stored alongside this report: live-samples.json, summary.json, service snapshots, loaded-source-comparison.json, storage samples and executor-cpu-profile.txt.
