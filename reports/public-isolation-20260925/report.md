# Public website isolation deployed — 2026-09-25

The production domains `dekings.org` and `www.dekings.org` now use the isolated,
read-only public website. The user explicitly selected removal of public owner
controls. The pre-existing service account `public-web` (UID 997, GID 987) runs the
website; Caddy still runs as `caddy`.

## Deployment

- New system service: `project15-public-site.service`, active and enabled.
- New trusted root user service: `project15-public-export.service`, active and enabled.
  Root lingering was already enabled and remains enabled.
- Old root user service: `project15-public-dashboard.service`, stopped and disabled.
- Caddy's only routing change: `127.0.0.1:8001` → `unix//run/project15-public/http.sock`.
- Port 8001 no longer listens. Private port 8000 remains loopback-only.
- Public code: `/srv/public-web/project15`, root-owned, readable by `public-web`.
- Sanitized data: `/var/lib/project15-public`, root-owned, group `public-web`,
  directory 2750 and files 0640. The website cannot replace or modify these files.
- Public runtime: a fresh Python 3.12 venv with only the 13 pinned distributions
  in `deploy/public-web/requirements.txt`, the public server and four static files.
  The offline package cache was incomplete, so `scripts/stage_public_site.py`
  copied installed dependency files from the tested environment, checking versions
  and recording SHA-256 hashes for 438 staged files. No trading package, private
  data, `.env`, owner hash or exchange credentials were copied.

The publisher polls fixed private GET routes, applying the existing public field
allowlist before atomic publication. Snapshots refresh every five seconds after
each completed cycle; histories refresh every sixty seconds after each completed
cycle. Public requests read only these files and cannot trigger private work.
The new app has no owner-control routes, outbound client, or arbitrary file route.

## Applied service restrictions

The website has no Linux capabilities, cannot gain new privileges, has a read-only
filesystem, cannot see `/root` or `/run/user`, and has private temporary/device and
network namespaces. Only AF_UNIX socket creation is permitted. It cannot connect
to host loopback services or the internet. Caddy reaches its filesystem Unix socket.
Resource ceilings are 256 MiB RAM, 64 tasks and 50% of one CPU core; Uvicorn limits
concurrency to 32. The trusted publisher has separate resource limits and no public
listener. It remains privileged under the trading account; this change does not
claim to migrate all root services.

## Verification

- `isolation-checks.json`: all nine access checks passed in the website's mount
  and network namespaces, using UID/GID `public-web` and no supplementary groups.
  Exchange credentials, owner hash, trading journal and executor socket were
  inaccessible; code/data were not writable; host ports 8000 and 2019 were
  unreachable; public data was readable.
- Live process `/proc` status: UID/GID 997/987, effective capabilities zero,
  `NoNewPrivs=1`, seccomp filtering active. Namespace probes do not themselves
  inherit the service's seccomp filter; the actual process status and systemd
  restrictions were checked separately.
- `staged-http-checks.json`: fresh snapshot and history for all seven assets.
- `https-cutover.json` and `https-final-checks.json`: both hostnames served the
  new service over certificate-validated HTTPS via the local Caddy listener.
  Page assets and data returned 200; former controls returned GET 404 / POST 405.
  These are server-side hostname/TLS checks, not an external-network penetration test.
- `trading-services-before.json` equals `trading-services-after.json`: the private
  dashboard, executor and all seven collectors remained active with unchanged PIDs.
- New service/publisher startup logs were clean at final verification.
- `systemd-security.txt`: systemd exposure score 2.5, rated OK; this heuristic is
  not a complete security audit.

## Tests and existing failures

Focused new/legacy public tests: **38 passed**, with two dependency deprecation
warnings. Ruff passed for all four new Python source/test files. Service units and
the proposed Caddyfile validated successfully.

Full suite: **1,929 passed, 46 failed, 3 skipped**, two warnings, 443.76 seconds.
See `tests-full.txt`. Failures are in `test_bleep_probability.py`,
`test_commodities.py`, `test_probability_exit_disabled.py` and
`test_strategy_settings.py`. They expect take-profit 0.99 or entry maximum 0.97,
while the pre-existing edited presets disable take-profit and use entry maximum
0.96. The isolated configuration failure was reproduced separately in
`existing-config-failure.txt`. No strategy/configuration files were changed for
this migration, and none of the new public tests failed. This is not a clean
full-suite result; those existing expectation/configuration differences remain.

## Operations and limits

See `docs/PUBLIC_WEBSITE_ISOLATION.md` for installation, updates, service commands,
data freshness, resource limits and recovery. The exact original and proposed
Caddyfiles are retained here. The cutover script restored the original routing
automatically if initial HTTPS validation failed; the successful cutover did not
need rollback.

Owner controls remain available only on the private dashboard through SSH. The
legacy root public command still exists in the source checkout for existing tests,
but is not in the new public installation and its service must remain disabled.
Websites sharing `public-web` are not isolated from each other by UID alone; every
future site needs suitable service restrictions. Dependency maintenance, SSH
hardening and network-level denial-of-service protection are separate work.
