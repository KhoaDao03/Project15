# Live-only cloud deployment

Research logging v2 was deployed on 2026-09-20. See
[deployment status](RESEARCH_LOGGING.md#deployment-status) for the current fleet.

For a walkthrough from a fresh Ubuntu VPS through domain/HTTPS and passcode setup,
see [Step-by-step IONOS VPS setup](IONOS_VPS_SETUP.md).

This profile runs four signal collectors, one real-order executor and one shared
dashboard on a Linux server with systemd. It starts no paper simulation workers,
records no raw feed tapes, and needs neither PostgreSQL nor PyArrow. SQLite files
and the real-order journal live on persistent local disk. Tests and research tools
remain in the repository for development; they are not running cloud services.

The four crypto presets use only Bleep ATR finish probability. See
[the model](PROBABILITY_MODEL.md) and [current settings](ACTIVE_PAPER_SETTINGS.md).
Old probability-mode configuration files are not accepted; prepare fresh configs.
Real-order exit rules are preserved. A new deployment starts
with live automation disabled. The dashboard's per-asset confirmation enables it;
the execution journal preserves those choices across restarts. Restarting the
dashboard does not restart the order executor.

## Prepare a fresh server

Use a dedicated Linux account with this reviewed checkout at `~/Project15`, Python
3.12 and uv installed. Deploy the reviewed working tree, including its new files;
an old Git commit will not contain local changes. Exclude `.env`, `.venv`, `data`,
private keys and local caches when transferring code. Keep research archives out of a fresh runtime transfer.

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

The public app on loopback **8001** shows selected market, reference and trading
results. Publishing its domain makes those results public. It has no manual-order,
strategy-editing or arbitrary proxy routes. Start it after installing the units:

```bash
systemctl --user enable --now project15-public-dashboard
```

For local preview use `.venv/bin/btc15 public-dashboard`. Follow
[VPS HTTPS setup](IONOS_VPS_SETUP.md#optional-public-website) for DNS and Caddy.
Expose only SSH and proxy ports 80/443; **never proxy the private app on 8000**.

The viewer polls fixed private GET endpoints every five seconds and exposes only
selected fields. It does not load exchange credentials, ledgers or the executor.
It shares the service account, so this is an application boundary, not OS isolation.
When upstream is unavailable it marks the last snapshot stale, or returns 503
before the first snapshot. A working viewer does not establish healthy trading.

### Enable owner controls with a passcode

After runtime preparation, on the server:

```bash
.venv/bin/python scripts/set_public_shutdown_passcode.py
systemctl --user restart project15-public-dashboard
```

Use a private 16–256-character passcode and HTTPS. The tool stores an owner-readable
salted PBKDF2-SHA256 hash (600,000 iterations); the service reads
`PROJECT15_SHUTDOWN_SECRET_FILE`. Without it, owner controls are disabled/hidden.
Repeat the command to rotate the passcode. Updated units need copying and daemon-reload.

Unlock authorizes confirmed live buy enable/disable, quantity 1–20, and safe shutdown
of one configured asset or all bots. It expires exactly 60 seconds after authentication;
actions do not extend it, and expiry does not undo saved settings. Tokens stay in
browser memory; a viewer restart invalidates them. Neither token nor passcode belongs
in URLs or browser storage. Same-origin JSON/body limits and a global five-attempts-
per-minute unlock limit apply; forwarded IP headers do not bypass that limit.

Revision checks, stale data and unavailable execution can block edits. Disabling
buys preserves exits; quantity changes affect future orders. The
[daily loss guard](LIVE_AUTOMATION.md#daily-live-loss-guard) still applies.

Safe shutdown never liquidates or overrides managed-position/unresolved-order
guards. One-asset shutdown leaves other collectors running. Require a positive
acknowledgment, not an upstream disconnect; inspect uncertain outcomes privately.
Allowed shutdown disables entries and cooperatively stops collectors; execution
may remain running. Public controls cannot start services. Viewer status is in
memory and can remain stale after shutdown.

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

Ordinary raw feed capture is disabled. Optional [v2 research capture](RESEARCH_LOGGING.md)
can record inputs/decisions when enabled separately on approved collector/executor
processes; without it this profile cannot replay every skipped decision.
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
