# Project15 VPS maintenance

For installation and startup, see [VPS setup](IONOS_VPS_SETUP.md).
Run server commands as the account that owns `~/Project15` (for example,
`root` for `/root/Project15`, or `project15` for `/home/project15/Project15`).

See [deployment status](RESEARCH_LOGGING.md#deployment-status) for the deployed logging changes.
Documentation edits need no restart.

### Controls and planned shutdown

Use an SSH tunnel for private controls; optional public owner controls are described
in [cloud setup](CLOUD.md#enable-owner-controls-with-a-passcode). Their 60-second
unlock permits saved policy changes and safe shutdown, not service starts.
Disabling buys preserves exits; quantities affect future entries only.

Safe shutdown requires positive acknowledgment and never liquidates positions.
Managed exposure/unknown orders can block it. After an approved restart, check
health and saved policies before re-enabling buys. Ordinary restarts preserve policies;
safe shutdown switches them off. Keep heavy replay workloads off the live host.

### Change the passcode

```bash
cd ~/Project15
.venv/bin/python scripts/set_public_shutdown_passcode.py
systemctl --user restart project15-public-dashboard
```

This rotates the hash and invalidates existing unlocks.

### Inspect services and storage

```bash
systemctl --user --failed
journalctl --user -u project15-public-dashboard -n 60 --no-pager
journalctl --user -u project15-execution -n 60 --no-pager
sudo journalctl -u caddy -n 60 --no-pager
df -h ~/Project15
du -sh ~/Project15/data/cloud
free -h
```

The collector stops below its configured 1 GiB free-space reserve. Keep order
journals, positions and settlement evidence. Do not delete them to reclaim space.

### Backups and updates

Keep backups off the VPS. For a consistent complete copy, first perform safe
shutdown with no managed live positions, then stop the remaining executor and
visitor processes before archiving `data/cloud`:

```bash
systemctl --user stop project15-execution project15-public-dashboard
cd ~/Project15
umask 077
tar -czf "$HOME/project15-cloud-backup-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" data/cloud
```

**Only run this after confirmed safe shutdown**, not while the bot is managing a
trade. This archive contains credentials and the shutdown hash; store it privately
and transfer it securely off the VPS. Back up the private key separately in secure
storage. Use the [startup commands](IONOS_VPS_SETUP.md#already-installed-start-here) when maintenance is complete.
For online backups, use SQLite's backup API; copying active database files alone
can omit WAL contents and is not a consistent fleet-wide backup.

For software updates, back up first, shut down safely, transfer the reviewed code,
run `uv sync --locked --no-default-groups --python 3.12`, update changed service files,
and run `systemctl --user daemon-reload` before starting services. Do not rerun
`prepare_cloud.py` over an existing runtime. Configuration changes require matching
runtime/checkpoint versions; do not edit them ad hoc to bypass startup checks.

This setup includes service restarts, but **does not configure external failure
alerts or scheduled off-server backups**. Arrange those separately before relying
on unattended operation. Hosting does not eliminate exchange/network outages.

## Deploy changes and restart the affected services

Run these commands **on the VPS as the account that owns the services**. Test and push the intended commit
from your local computer first. Deploy a reviewed commit, not an unreviewed moving
branch. Do not run test/replay workloads against production databases.

### Git prerequisite

The initial upload in the setup guide uses `rsync` and does not create a Git checkout.
`git pull` below applies only if `~/Project15` has been set up as a checkout of your
repository. Check with `git rev-parse --show-toplevel`. If it is not a checkout,
continue using the reviewed source transfer procedure until you have converted it
without overwriting `data/cloud` or credentials. Do not clone over a running project
or delete it to create a checkout.

For a Git checkout, review `git status --short` first. If it reports local changes,
resolve them deliberately; do not use `git reset --hard` or `git clean` to force a
deployment. Keep `data/`, `.env`, private keys and credential files out of Git.

### Which service needs to reload?

| Change | Service action |
| --- | --- |
| Only visitor code in `public_dashboard.py` or `public_static/` | Restart `project15-public-dashboard`; live trading continues. |
| Only private dashboard display code, with no shared trading/dependency changes | Restart `project15-dashboard`; live execution and collectors continue. The visitor may briefly show stale data. |
| Strategy engine, collectors, order execution, shared modules, or uncertain scope | Use the full safe deployment below. |
| `pyproject.toml` or `uv.lock` | Use the full safe deployment; synchronize dependencies while services are stopped. |
| A file under `deploy/cloud/*.service` | Copy the changed unit, run `daemon-reload`, then start/restart its affected service. Trading-unit changes require safe deployment. |
| Saved strategy presets | A pull/restart alone does not apply them to `data/cloud`; use an audited runtime configuration migration. |
| Documentation only | Pull the commit; no service restart. |

Review the **whole commit** when selecting a procedure. A dashboard fix bundled
with shared engine or dependency edits requires the full procedure.

### Visitor-dashboard-only deployment

Use this only when the commit changes the visitor dashboard and its documentation,
without dependencies or shared trading code:

```bash
cd ~/Project15
git status --short
git pull --ff-only
git log -1 --oneline
systemctl --user restart project15-public-dashboard
systemctl --user is-active project15-public-dashboard
curl -fsS http://127.0.0.1:8001/api/view
```

Review the resulting commit before the restart. Open the HTTPS site and refresh the
page to load the new frontend files. Existing one-minute owner unlocks are revoked
by the viewer restart; enter the passcode again. Trading services keep running.

For a **private-dashboard-only** change, use the same Git checks, then:

```bash
systemctl --user restart project15-dashboard
systemctl --user is-active project15-dashboard
curl -fsS http://127.0.0.1:8000/api/fleet
```

This does not restart the executor. Do not use a dashboard-only procedure for
changes to shared modules or the virtual environment.

### Full safe deployment for trading or dependency changes

1. Wait for managed live positions and unresolved orders to finish, then request
   **safe shutdown** through the private dashboard or unlocked visitor control.
   Require positive confirmation. A 409 or unknown result means you must resolve
   the condition before proceeding. Shutdown disables live entries; it does not
   liquidate positions.
2. Stop the remaining processes. The following commands are for **after confirmed
   safe shutdown**, not an emergency stop while contracts are held:

   ```bash
   systemctl --user stop project15-signal@BTC project15-signal@ETH project15-signal@SOL project15-signal@XRP project15-execution project15-dashboard project15-public-dashboard
   ```

3. Back up the runtime as described above. Record the current code revision, then
   pull the tested change and synchronize dependencies:

   ```bash
   cd ~/Project15
   git status --short
   git rev-parse HEAD
   git pull --ff-only
   git log -1 --oneline
   "$HOME/.local/bin/uv" sync --locked --no-default-groups --python 3.12
   ```

   If Git or dependency installation fails, leave trading stopped and fix that
   error before continuing. Do not continue a partially completed deployment.

4. Refresh the supplied service definitions, then start the fleet:

   ```bash
   cp deploy/cloud/*.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user start project15-signal@BTC project15-signal@ETH project15-signal@SOL project15-signal@XRP
   systemctl --user start project15-execution project15-dashboard project15-public-dashboard
   ```

5. Verify all seven app services, allow feeds to warm up, and check health:

   ```bash
   systemctl --user is-active project15-signal@BTC project15-signal@ETH project15-signal@SOL project15-signal@XRP project15-execution project15-dashboard project15-public-dashboard
   journalctl --user -u project15-execution -n 50 --no-pager
   cd ~/Project15
   for asset in BTC ETH SOL XRP; do
     .venv/bin/btc15 --data-dir "data/cloud/$asset" paper-health --run-id "$asset-live-signals"
   done
   curl -fsS http://127.0.0.1:8001/api/view
   ```

6. Review saved quantity/settings and fresh market data. Then explicitly re-enable
   the desired live policies through the private dashboard or the visitor's new
   one-minute owner unlock. A service starting successfully does not re-enable a
   policy disabled by safe shutdown. Observe initial activity and reconcile fills.

Caddy does not need restarting for Python/frontend changes. If you change its
configuration, validate and reload it using the commands in the [optional public website setup](IONOS_VPS_SETUP.md#optional-public-website).

If the new release fails, keep automation disabled. Reverting code may also require
compatible dependencies and a reviewed state migration. Do not blindly restore an
old order journal: orders or fills recorded after that backup must be reconciled.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `Failed to look up user project15` | Use `sudo loginctl enable-linger "$(id -un)"` from the account that owns the services. |
| `Failed to connect to bus` | Log in directly over SSH as the account that owns the services; use `systemctl --user` without sudo; verify lingering is enabled. |
| Collector keeps restarting | Read its journal; check key path/permissions, frozen configuration, network access and disk space. |
| Public site returns 502 | Check `project15-public-dashboard`, then `curl http://127.0.0.1:8001/api/view`; verify Caddy targets 8001. |
| Visitor shows stale data or 503 | Check the private dashboard, collectors and execution service; allow initial warmup. Staleness is expected after safe shutdown. |
| HTTPS certificate fails | Check DNS, including any AAAA record, both firewalls, and Caddy's journal. |
| Shutdown form is missing | Check passcode setup, the updated viewer service environment, and restart the viewer. |
| Shutdown returns 409 | A position/order still needs management. Leave its existing exit management running and retry when resolved. |
| Unlock returns 429 | Wait a minute; repeated attempts share one rate limit. |
| Unlock returns 403 with the right code | Use the HTTPS site's own form; check whether the passcode was rotated and the service restarted. Ensure the proxy preserves the public host and forwarded scheme. |
| A save or shutdown returns 401 | The one-minute unlock expired. Enter the passcode again; no expired request is forwarded. |
| Bots run but do not trade | Check live policies, collector health, entry filters and risk limits in the private dashboard. |

For implementation details, see [Cloud runtime reference](CLOUD.md).
