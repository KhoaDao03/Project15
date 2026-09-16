# Live-only cloud deployment

For a walkthrough from a fresh Ubuntu VPS through domain/HTTPS and passcode setup,
see [Step-by-step IONOS VPS setup](IONOS_VPS_SETUP.md).

This profile runs four signal collectors, one real-order executor and one shared
dashboard on a Linux server with systemd. It starts no paper simulation workers,
records no raw feed tapes, and needs neither PostgreSQL nor PyArrow. SQLite files
and the real-order journal live on persistent local disk. Tests and research tools
remain in the repository for development; they are not running cloud services.

The existing strategy and real-order rules are preserved. A new deployment starts
with live automation disabled. The dashboard's per-asset confirmation enables it;
the execution journal preserves those choices across restarts. Restarting the
dashboard does not restart the order executor.

## Prepare a fresh server

Use a dedicated Linux account with this reviewed checkout at `~/Project15`, Python
3.12 and uv installed. Deploy the reviewed working tree, including its new files;
an old Git commit will not contain local changes. Exclude `.env`, `.venv`, `data`,
private keys and local caches when transferring code. Do not copy the 53 GB local
research directory into a fresh deployment.

From `~/Project15`:

```bash
uv sync --locked --no-default-groups
.venv/bin/python scripts/prepare_cloud.py
```

The preparation command freezes the four current presets and creates
`data/cloud/manifest.json`. It refuses an existing output directory. Future preset
edits do not change those frozen files. The collectors refuse a paper portfolio or
an incompatible configuration in their signal database.

Create `data/cloud/credentials.env` with these two values, storing the private key
separately on the server:

```text
KALSHI_API_KEY_ID=your-key-id
KALSHI_PRIVATE_KEY_PATH=/absolute/path/to/private-key.pem
```

Restrict both files to the service account (`chmod 600`). The existing environment
guards remain `TRADING_MODE=PAPER` and `ENABLE_LIVE_TRADING=false`: these control the
legacy strategy executor. Real orders use the separate execution service and its
explicit dashboard controls. Changing those environment guards is not required.

Install and start the user services on the selected server:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/cloud/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
sudo loginctl enable-linger "$USER"
systemctl --user enable --now project15-signal@BTC project15-signal@ETH project15-signal@SOL project15-signal@XRP project15-execution project15-dashboard
```

These services restart after failures with a 15-second delay. Explicit stops and
clean dashboard shutdowns remain stopped until started again or the user manager
restarts. Lingering lets the enabled services start at boot and survive logout.
See [systemd service restart behavior](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html#Restart=).

## Private dashboard

The dashboard stays bound to localhost. From your own computer:

```bash
ssh -N -L 8000:127.0.0.1:8000 user@server
```

Open `http://127.0.0.1:8000`. Keep port 8000 closed to the public internet; the app
has local request checks but no remote login system. The live-only manifest hides
the paper/backtest and archive selectors. Existing internal record-mode names are
retained for compatibility; fills shown through the live journal are real fills.

## Optional public view-only dashboard

The separate `public-dashboard` app shows all four markets, reference prices,
recorded wins/losses and net P&L, open-position counts, and the latest five trades
per asset. These trading results become public when you publish its domain.
It has no manual-order, strategy-editing or generic proxy routes. Optional owner
controls allow live buy enable/disable and 1–20 contracts per entry for each asset,
plus safe shutdown. `POST /api/unlock` checks the passcode and issues a token valid
for exactly 60 seconds. Both `POST /api/control` and `POST /api/stop` require it.
All other non-GET/HEAD requests are rejected on the server, including direct API calls.
The original dashboard remains private and retains the controls you use over SSH.

```bash
systemctl --user enable --now project15-public-dashboard
```

This assumes the service files were copied and `daemon-reload` was run as above.
For a local preview, run `.venv/bin/btc15 public-dashboard` and open
`http://127.0.0.1:8001`. On a remote host you can forward that port through SSH too.

The viewer binds to loopback port **8001**. Point your domain's DNS to the VPS,
install Caddy, and use `deploy/cloud/Caddyfile.example` as its site configuration,
replacing `dash.example.com` with your actual domain. Expose only SSH and the
reverse proxy's HTTP/HTTPS ports (80/443); keep 8000 and 8001 private. **Never point
the public reverse proxy at port 8000 or route private `/api` paths through it.**
The example sends every public path to the view-only app, including unknown paths,
which return 404. Caddy handles HTTPS once DNS and network access are configured.

The viewer reads fixed GET endpoints on the private dashboard every five seconds
and publishes only selected display fields. Browser requests never choose an
upstream URL or forward headers/cookies. Only authenticated owner actions trigger
fixed upstream POSTs for live settings or shutdown; snapshot reads come from a periodic cache.
It does not load trading credentials, open ledgers, or connect to the executor.
It shares the service account in this minimal deployment; it is an application
boundary, not OS-level isolation against a compromised process.
When the private dashboard is unavailable, the viewer labels its last snapshot
stale, or returns 503 if no snapshot exists. An active viewer does not prove that
trading is healthy. Heavy public traffic still consumes server/network resources.

### Enable owner controls with a passcode

On the server, after preparing `data/cloud`, run:

```bash
.venv/bin/python scripts/set_public_shutdown_passcode.py
systemctl --user restart project15-public-dashboard
```

If updating an existing service installation, first copy the updated service file
to `~/.config/systemd/user/` and run `systemctl --user daemon-reload`.
Enter a unique 16–256-character passcode privately in the terminal (prefer a
password-manager-generated value). Do not put it in chat, source code or a URL.
The tool stores only a salted PBKDF2-SHA256 hash (600,000 iterations), in a file
readable only by its owner. Run it again and restart the viewer to rotate the code.
The service reads `PROJECT15_SHUTDOWN_SECRET_FILE`; if the file is absent, owner controls
are disabled and their panel is hidden. Use HTTPS on the domain before entering it.

Enter the passcode in **Unlock for 1 minute**. The server checks same-origin JSON
requests, a small body limit, and a global maximum of five unlock attempts per
minute (in memory, reset when the viewer restarts). It does not trust forwarded
IP headers for this limit. The passcode is cleared from the input, never included
in snapshots, and never sent upstream. A random authorization token is held only
in browser memory; neither token nor passcode goes in a URL or local storage.

The unlock expires **60 seconds after successful authentication**, independently
of browser timing. Saves and shutdown do not extend it. At expiry, enter the same
passcode again to obtain a fresh unlock. Expiry does not undo a saved policy or
cancel an already accepted shutdown. Restarting the viewer invalidates all tokens.

Choose an asset, review its saved policy, set new buys on/off and 1–20 whole
contracts, confirm the real-money settings, and click Save. These settings apply
to that asset and future markets. Disabling buys preserves automatic exits; a
quantity change does not resize contracts already held. Revision checks reject
settings changed concurrently in the private dashboard. Stale snapshots or an
unavailable executor block live edits. Re-enabling buys can cause real orders
when the existing strategy and risk checks pass; it does not start stopped services.

An accepted request uses the existing private dashboard's safe shutdown procedure.
**Live positions or unresolved orders still under management block shutdown.** It
does not liquidate positions or override this guard. When allowed, live entries
are disabled and collectors shut down cooperatively; the order executor process
may remain running, with automation disabled. Restarting trading requires the
private controls and services. The viewer reports completion only after a positive
shutdown acknowledgement, not merely because the private dashboard disconnects.
An uncertain result requires checking the private dashboard/services. Status is
held in memory and resets if the viewer restarts. Public viewing remains available
with stale snapshots after the private dashboard stops.

## Operation and acceptance

```bash
systemctl --user status 'project15-signal@*' project15-execution project15-dashboard
journalctl --user -u project15-execution -n 100 --no-pager
.venv/bin/btc15 --data-dir data/cloud/BTC paper-health --run-id BTC-live-signals
```

The health command also supports live signal collectors. Require fresh references,
completed warmup, healthy recovery and fresh strategy decisions for every asset.
Then confirm each desired live asset in the dashboard. A process being active does
not by itself establish healthy trading. Check CPU, RSS, processing lag and daily
disk growth before choosing the smallest server size; no cloud load measurement
has yet established a minimum machine size.

Run a connected acceptance session across market rollover and an orderly service
restart. Check journal reconciliation and fills against the exchange. Test abrupt
failure recovery in an isolated test environment before relying on unattended use.
The supervisor restarts failed processes; feed recovery and health gates handle
stale/disconnected inputs. This does not guarantee uninterrupted exchange access.

Use the dashboard's safe shutdown before stopping all services. The execution
service's shutdown preparation disables new entries and retains order state.
Do not independently stop collectors while expecting open positions to retain
fresh stop monitoring. Closing the browser or SSH tunnel leaves the bots running.

Raw feed capture is disabled, so this profile cannot replay every skipped decision.
It retains current status/evaluations, contract/settlement evidence, reference cache
and the real-order journal. Those durable records still grow over time; monitor disk
usage and back up `data/cloud` using SQLite's backup API or after a clean shutdown.
The collector stops below a 1 GiB free-space reserve. Do not delete order journals,
unresolved positions or settlement evidence to recover space.

This is a fresh deployment procedure, not a migration of the currently running
account. Before switching hosts, reconcile existing orders and positions and ensure
the old executor cannot also trade. Transfer an existing execution journal only as
part of an explicitly reviewed cutover; do not reset it to bypass ownership checks.

## Development extras

`uv sync --locked --extra dev` retains the complete local test environment.
Parquet capture/export needs `--extra research`; PostgreSQL needs `--extra postgres`.
These are [optional uv extras](https://docs.astral.sh/uv/concepts/projects/sync/#syncing-optional-dependencies).
