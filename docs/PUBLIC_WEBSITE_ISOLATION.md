# Isolated read-only public website

The production public website runs as `public-web`, a system account with a locked
password, no login shell and no sudo rights. Caddy remains under its existing
`caddy` account. Trading services and the private dashboard retain their existing
account and runtime state.

```text
Collectors / executor → private dashboard (127.0.0.1:8000)
                                ↓ fixed-schedule GET requests
                         trusted data publisher
                                ↓ atomic sanitized JSON files
                       /var/lib/project15-public
                                ↓ read-only access
Internet → Caddy HTTPS → Unix socket → public-web website
```

The public process cannot use IP networking. It has a private network namespace,
permits only Unix-domain sockets, hides `/root`, and cannot read the trading
credentials, ledgers or executor socket. It has no owner-control routes, passcode
hash, exchange keys, private-API client, or trading package in its installation.
The website uses only FastAPI/Uvicorn and their runtime dependencies.

Creating the account alone does not provide these restrictions. Other websites
sharing `public-web` must receive their own reviewed service restrictions; a shared
UID is not an isolation boundary between websites. Do not add the account to sudo,
trading, or Caddy credential groups.

## Files and ownership

| Path | Owner/group | Use |
| --- | --- | --- |
| `/srv/public-web/project15` | `root:public-web` | Read-only website code, static assets, isolated venv and file hash manifest |
| `/var/lib/project15-public` | `root:public-web` | Published data, directory mode 2750, files 0640 |
| `/run/project15-public/http.sock` | `public-web:public-web` | Public HTTP socket, recreated by the service |
| `/etc/systemd/system/project15-public-site.service` | `root:root` | System-level isolated website service |
| `~/.config/systemd/user/project15-public-export.service` | Trading account | Trusted scheduled publisher, no public listener |

The exported-data directory is outside `/var/lib/public-web` deliberately: the
public account owns that general runtime directory and must not be able to replace
or rename the trusted export directory. Its parent `/var/lib` is root-owned.
The public HTTP socket allows local clients, including Caddy, to connect; those
clients see exactly the same read-only API as internet visitors.

## Data and resource limits

The trusted publisher uses fixed localhost GET routes. It reuses the existing
explicit public field projection; it never exports private run IDs, order IDs,
raw records, credentials, or arbitrary private errors. It refreshes the dashboard
every five seconds after each completed cycle and histories every sixty seconds
after each completed cycle. Visitor requests never trigger upstream work.

Each complete file is published using an atomic rename. Failed refreshes preserve
the last complete file. Dashboard data older than twenty seconds and history older
than 120 seconds are marked stale. Before an initial export, the website returns
a generic 503. A healthy web process alone does not prove healthy trading.

Histories are fetched in pages of at most 500 rows with a thirty-second budget per
asset and a 50,000-row ceiling. An incomplete/over-budget refresh does not replace
the prior complete export or silently truncate its history. Monitor publisher
warnings and stale history as the dataset grows. Pagination occurs on the public
copy and serves at most 100 rows per request. Rapidly changing trades can move
between upstream pages during a refresh; this is a public display, not an audit
snapshot of the trading database.

### Public history request protection

`public_site.py` applies a global token bucket to valid history requests: burst
capacity ten, refilling at five requests per second across all assets and visitors.
At most two history operations may be active. Excess requests are rejected
immediately with HTTP 429 and `Retry-After: 1`; they are not queued for a filesystem
worker. These limits apply to the single public Uvicorn process. Adding workers
would multiply the limits and requires a separate review.

Forwarded IP headers cannot bypass this global budget. The limit deliberately does
not identify individual visitors behind Caddy's Unix socket. An abusive visitor can
consume the shared history allowance, so this protects server work rather than
guaranteeing fair access or protection from a network-level DDoS. The snapshot and
static routes do not use the history budget; the existing service-wide Uvicorn,
CPU and memory limits still apply to all routes.

Two recently used asset histories are cached after JSON decoding. Pagination and
arbitrary offsets do not create additional cache entries. The reader checks the
opened file's device, inode, size and nanosecond modification time on each admitted
request, so the publisher's atomic replacement invalidates the cached data.
Freshness is recalculated on every response, including cache hits. Missing,
malformed or incomplete exports produce generic 503 responses rather than silently
serving a superseded cached version.

Files larger than 8 MiB are rejected before decoding (and bounded reads also guard
against unexpected growth). Histories over 50,000 rows or with inconsistent row
counts are rejected. The 8 MiB serving limit can be reached before the publisher's
50,000-row ceiling; monitor 503 responses and review the storage format before
increasing those budgets. One loader lock prevents duplicate simultaneous decodes;
only two admitted operations can hold/wait for that lock. If an HTTP request is
cancelled, its capacity is retained until its filesystem thread actually finishes.

The public unit limits memory to 256 MiB, tasks to 64 and CPU to half of one core.
Uvicorn limits concurrent connections/tasks to 32. The publisher is separately
limited to 256 MiB, 32 tasks and a quarter of one CPU core. These are initial limits;
observe real usage before changing them. They limit local impact, not network DDoS.

## Initial installation (root-owned Project15 deployment)

The `public-web` account must already exist. Run these commands as root. Use a
fresh destination venv; do not copy a venv from `/root` or make `/root` traversable.

```bash
cd /root/Project15
install -d -o root -g public-web -m 0750 /srv/public-web/project15
install -d -o root -g public-web -m 2750 /var/lib/project15-public
uv venv --python /usr/bin/python3.12 /srv/public-web/project15/.venv
.venv/bin/python scripts/stage_public_site.py /srv/public-web/project15
chown -hR root:public-web /srv/public-web/project15
chmod -R g+rX,o-rwx /srv/public-web/project15
install -o root -g root -m 0644 deploy/public-web/project15-public-site.service /etc/systemd/system/
install -o root -g root -m 0644 deploy/public-web/project15-public-export.service /root/.config/systemd/user/
systemd-analyze verify /etc/systemd/system/project15-public-site.service
systemd-analyze --user verify /root/.config/systemd/user/project15-public-export.service
systemctl daemon-reload
systemctl --user daemon-reload
systemctl --user start project15-public-export
systemctl start project15-public-site
```

The staging script copies only the installed distribution files pinned in
`deploy/public-web/requirements.txt`, plus the public server and four static files.
It checks package versions and records SHA-256 file hashes in the destination
`manifest.json`. It does not download packages or copy the project's `.env`, data,
keys, Git history, CLI, collectors or executor. Review dependency changes and stage
them in a fresh venv during future upgrades.

Allow the publisher and website to finish starting, then check:

```bash
curl --unix-socket /run/project15-public/http.sock http://localhost/api/view
curl --unix-socket /run/project15-public/http.sock http://localhost/api/history/BTC
runuser -u caddy -- curl --unix-socket /run/project15-public/http.sock http://localhost/
```

Require seven expected assets, fresh exports, working static files, no control
routes, and confirmed filesystem/network isolation before switching traffic.
The dashboard service itself need not restart; neither do collectors or execution.

## Caddy cutover

Back up the existing Caddyfile, preserving other sites. Change only this site's
upstream:

```caddyfile
dekings.org, www.dekings.org {
    reverse_proxy unix//run/project15-public/http.sock
}
```

Validate the proposed file before installing it. After installation:

```bash
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl reload caddy
curl --resolve dekings.org:443:127.0.0.1 https://dekings.org/api/view
```

Check both hostnames, static files and histories through HTTPS. GET requests for
`/api/unlock`, `/api/control`, `/api/stop` and private endpoints must return 404;
POST requests return 405. The website has no owner controls even when callers
forge local Origin headers. Then retire the old root-owned public listener:

```bash
systemctl enable project15-public-site
systemctl --user enable project15-public-export
systemctl --user disable --now project15-public-dashboard
```

Keep port 8000 private and do not open a new public app port. The old 8001 listener
should disappear. Owner controls remain on the private dashboard through SSH.
The legacy `btc15 public-dashboard` command still exists for existing development
tests; it is not installed in the isolated public environment and must not be
re-enabled or exposed in this deployment.

## Operations and recovery

```bash
systemctl status project15-public-site --no-pager
systemctl --user status project15-public-export --no-pager
journalctl -u project15-public-site -n 50 --no-pager
journalctl --user -u project15-public-export -n 50 --no-pager
systemd-analyze security project15-public-site --no-pager
```

Update website code as the administrator, never as `public-web`. After a reviewed
code/static change, restage those files with root ownership and restart only
`project15-public-site`. Restart the publisher only if its code changes. Do not
restart collectors/execution for a public website change.

If export fails, fix the publisher or private dashboard; never grant the website
direct private-API access as a fallback. Keeping the site stale/offline preserves
the security boundary. A temporary routing rollback to port 8001 requires starting
the legacy viewer and restores its old root/control exposure, so prefer fixing the
new service. The deployment report retains the exact pre-cutover Caddyfile for
administrative recovery.
