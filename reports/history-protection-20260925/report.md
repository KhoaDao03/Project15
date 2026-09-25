# Public history request protection — 2026-09-25

## Scope and original finding

The original finding referred to `public_dashboard.py` forwarding each visitor's
history request into the private dashboard. That path is no longer exposed:
the earlier isolation deployment stopped/disabled the old root viewer, removed
its port-8001 listener, and changed Caddy to the isolated public-site Unix socket.
The current `public_site.py` reads sanitized, root-published files and cannot use
IP networking. Its publisher runs on a fixed schedule independent of visitors.

This change addresses the remaining public workload: before this patch, every
history request decoded the entire local history file before slicing a page.
Visitors still shared the website host's CPU/memory, even though private services
were no longer called. The legacy proxy code was not re-enabled or used in testing.

## Implemented protections

| Protection | Value and behavior |
| --- | --- |
| Request admission | Global token bucket: burst 10, refills at 5 requests/second |
| Active work | Maximum 2 history operations; reject excess immediately, no unbounded work queue |
| Overload response | HTTP 429 with `Retry-After: 1` |
| Client identity | No trust in `X-Forwarded-For`; changing claimed IPs cannot obtain additional capacity |
| Decoded cache | At most 2 recently used asset files, keyed by fixed supported assets, not visitor query strings |
| Cache refresh | Check the opened file's device/inode/size/mtime; atomic file replacements invalidate the cache |
| Decode coordination | One loader lock prevents duplicate simultaneous decodes |
| Export budget | At most 8 MiB per file and 50,000 rows; row count must match exported total |
| Response budget | Existing maximum 100 rows per page remains enforced |
| Freshness | Recomputed on every response, including cache hits; history over 120 seconds old is stale |
| Failure behavior | Missing/corrupt/oversized exports return generic 503, without private details or old-cache fallback |
| Disconnected callers | Keep capacity occupied until the underlying filesystem thread finishes |

Existing Uvicorn concurrency 32, service CPU quota 50% of one core, memory ceiling
256 MiB, restricted filesystem and network namespace remain in force. History
limits do not consume the snapshot/static endpoint budgets. They are per process;
the deployed service uses one worker. More workers would multiply the allowance.

The global limiter protects resource use; it does not promise fair access between
visitors. A malicious visitor may consume the shared history budget and make others
receive 429. Network-level DDoS protection and per-client fairness remain separate
concerns. If valid exports grow beyond 8 MiB, review the serving/storage design;
do not silently remove the limit. That byte limit can precede the publisher's
50,000-row export ceiling.

## Isolated before/after experiment

`benchmark.py` creates a synthetic 10,000-row history (~1.55 MB) and issues 100
simultaneous ASGI requests with different offsets and spoofed forwarded IPs.
Separate temporary systemd units had private network namespaces, a 25%-of-one-core
CPU quota, a 512 MiB memory ceiling and a 64-task ceiling. A Python audit hook also
rejected/logged any socket connection attempt. No production burst was performed.

The baseline is the already-isolated file-reading server, copied from the deployed
installation before this patch, not the old private-proxy server.

| Measurement | Before | After |
| --- | ---: | ---: |
| HTTP 200 | 100 | 2 |
| HTTP 429 | 0 | 98 |
| Full history decodes | 100 | 1 |
| Bytes submitted to JSON decoder | 154,785,600 | 1,547,856 |
| Burst CPU time | 4.464 s | 0.208 s |
| Burst elapsed time under CPU quota | 17.820 s | 0.810 s |
| Process peak RSS, including setup/recovery | 165,300 KiB | 61,804 KiB |
| Network connection attempts | 0 | 0 |
| Normal request after refill | 200 | 200 |
| Snapshot route after burst | 200 | 200 |

Every 429 included `Retry-After: 1`. These numbers show less work by rejecting
excess requests and caching a shared file; they are not equivalent-throughput
claims. They are one synthetic experiment, not an internet-scale load test.
Machine-readable results are in `benchmark-before.json` and `benchmark-after.json`.

## Regression tests

`tests-focused.txt`: 42 passed, two dependency deprecation warnings. This includes
all existing public-site/dashboard cases plus new rate-refill, forwarded-IP,
cache refresh/eviction, age, byte-limit, incomplete-export, concurrency and
cancellation regressions. The concurrency test blocks a filesystem reader,
verifies an additional caller is rejected promptly, confirms snapshots still
respond, cancels a caller, and verifies the slot is not released prematurely.

`tests-limits-final.txt`: all four limit/cache regression tests passed after adding
an explicit check that corrupt replacements cannot fall back to a previously
cached valid export. Ruff passed for the changed Python files.

Full suite (`tests-full.txt`): **1,933 passed, 46 failed, 3 skipped**, two warnings,
411.88 seconds. The exact set of 46 failed test IDs matches the previous isolation
deployment run (`test-comparison.json`); there are no newly failing tests. Those
existing failures concern the prior take-profit/entry-price preset changes. This
is not a clean full-suite result, and those unrelated differences remain unresolved.

## Deployment and live verification

Deployed at approximately **04:19 UTC, 2026-09-25**. Only
`/srv/public-web/project15/public_site.py` and its deployment hash manifest were
updated, using atomic replacements with `root:public-web` ownership and mode 0640.
The public service alone was restarted. The prior module and manifest were saved
for rollback; the install procedure would restore them if the startup/history
health check failed. Rollback was not required.

`deployment-verification.json` records certificate-validated HTTPS checks through
the local Caddy listener for both `dekings.org` and `www.dekings.org`: normal history
and snapshot requests returned 200 with fresh data, and a page size of 101 returned
422. No production traffic burst was sent. The installed source hash matches the
tested source. The process still runs as `public-web` with `PrivateNetwork=yes`
and `RestrictAddressFamilies=AF_UNIX`; sampled service memory was 37,974,016 bytes.

`services-before.json` and `services-after.json` are identical: the private
dashboard, executor, all seven collectors and the publisher remained active with
unchanged PIDs. Caddy routing and the fixed publisher schedule were unchanged.
The legacy root viewer remains stopped/disabled. Startup logs showed a clean
restart without errors.

Implementation: `src/btc15/public_site.py`. Regression cases:
`tests/test_history_protection.py`. Operational details and limits:
`docs/PUBLIC_WEBSITE_ISOLATION.md`, “Public history request protection”.
