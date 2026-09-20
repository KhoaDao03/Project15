# Portable research logging (not deployed)

This opt-in recorder supports future probability analysis and approximate strategy replay.
It does not place orders, change strategy settings, or run simulations. It is disabled by
default. The implementation has been merged into `cloud-deploy`; activation still requires
the operator's explicit deployment approval.

## Location and transfer

The deployment default is `research-logs/` relative to the project's working directory
(`/root/Project15/research-logs/` on the VPS). This entire folder is in `.gitignore`.
An explicit `BTC15_RESEARCH_LOG_DIR` can select an absolute path; custom locations must
also be kept outside tracked source files.

```
research-logs/
  BTC/
    20260920T...-unique-session-id/
      manifest.json
      source.json.gz
      status.json
      events-...jsonl.gz
      events-...jsonl.gz.part
  ETH/...
```

Each session contains its own configuration, model multipliers, source snapshot/hash,
and timestamped version-1 JSONL events. Keep the manifest and source with the events.
`status.json` exposes queued bytes, dropped records, errors and clean shutdown status.
Completed segments rotate every ten minutes. `.part` files are still being written or
were interrupted; exclude them from transfers and analyses. An incomplete gzip file
must not be interpreted as complete coverage. Copy files rather than moving active
sessions out from under the writer.

After deployment, copy archives to a local machine using, for example:

```bash
rsync -av --exclude='*.part' --exclude='.retention.lock' \
  user@YOUR_VPS:/root/Project15/research-logs/ ./research-logs/
```

Use the VPS account/path you normally use. Do not use `--delete`: local archives should
survive VPS retention cleanup. Repeat after rotation to retrieve newly completed files.
The currently active status file is advisory and can change during transfer.

## Recorded data

* Reference input events with original source/receipt information, including startup
  reference history and indicator seed inputs. Existing feed connections are reused.
* Metadata, lifecycle, settlement, connection and error input events.
* Top-five levels on each outcome's bid ladder, corresponding best asks, timestamp and
  freshness, once per second per open market. Changes across 55-cent and 99-cent bid
  thresholds are also captured between samples, including transitions into invalid data.
* Existing probability evaluations, including features/ATR, raw and capped model outputs,
  book, all rejection reasons, config, and timestamps. Recorded outside the entry window
  as well as inside it. Evaluations are sampled at one second plus changes across the
  83% capped-confidence threshold for either side (YES then NO). Initial observations
  are marked as changes; do not assume they prove an upward crossing from below.
* Local settlement/evidence and real-order journal snapshots, polled every 30 seconds on
  the writer thread. These include existing historical records on startup and later
  updates. Deduplicate orders by ID and `updated_at`; settlements by market/evidence ID.
  These are journal snapshots, not every intermediate exchange event. Account user IDs
  are excluded. Journals are read-only and no new authenticated network calls are made.

The full evaluation describes the last actual model computation, not a new calculation
performed by the recorder. Use its own timestamps and quality fields. Missing/stale data
and `recording_gap` events invalidate coverage for reliable replay. Conservatively exclude
a session with dropped records unless the missing interval can be independently bounded. A confidence
crossing is observational, not permission to trade; commodity entry thresholds are unchanged.

## Resource limits

One daemon writer per collector; no separate service or database server. The producer
serializes selected records and uses a nonblocking queue capped at 8 MiB of serialized
payload AND 8,192 records per asset (up to 56 MiB payload across seven assets, plus Python
object/serialization overhead). Overflow drops recording data and explicitly reports it;
trading continues. Disk compression, file operations, journal reads and retention run on
the writer, not the trading thread. Serialization/sampling still has a CPU cost that must
be measured on the VPS before broad activation.

Gzip level 1, roughly one-second flushes, ten-minute segments. Closed event segments are
removed oldest-first after seven days or when the shared archive allowance exceeds
20 GiB (including source/manifest sizes). Open segments are not deleted, so this is a
soft cap with temporary overshoot. Expired empty-session metadata is cleaned up too.
Interrupted `.part` files are deliberately retained for inspection and require manual
cleanup. Never point this recorder at a directory containing unrelated data.

If free disk is below 30 GiB, event recording pauses and dropped records accumulate;
trading is unaffected. The writer checks disk/retention about every five seconds. This
reserve is not a guarantee against other processes filling the disk between checks.
When writing resumes, a `recording_gap` reports loss. Permanent writer failures also
appear in collector status/service warnings; status.json may be unavailable on disk errors.
Shutdown drains for at most two seconds; unfinished sessions must be treated as incomplete.

## Deployment gate

No environment variables, active configuration files or services are changed by this
implementation. A future approved deployment would explicitly set
`BTC15_RESEARCH_LOG_ENABLED=1` and `BTC15_RESEARCH_LOG_DIR=/root/Project15/research-logs`
for selected signal collectors and restart them safely. Keep the flag unset to disable.
Start with one collector and measure CPU, RSS, disk growth, drop counters and live
processing lag before enabling all seven. No activation commands are run by the tests.

## Analysis limits

This is the logging foundation, not a completed backtester. It supports recalculating
model inputs and comparing sampled execution opportunities. One-second/top-five books
cannot reconstruct queue position, all book changes or guaranteed fills. Future replay
must respect receipt times, warm-up, fees, latency, invalid intervals and depth limits.
The recorded source/config explain original decisions; alternative settings must run
through compatible strategy code. A short live recording trial and baseline replay
validation remain required before treating simulated P&L as dependable.
