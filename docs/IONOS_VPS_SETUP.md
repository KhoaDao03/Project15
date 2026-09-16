# Project15 on an IONOS VPS: setup guide

This guide sets up a **fresh Ubuntu 24.04 LTS server** with:

- Four live signal collectors: BTC, ETH, SOL and XRP.
- One order executor, initially disabled for new live entries.
- A private dashboard with full controls, accessible through SSH.
- A public visitor dashboard with optional passcode-protected live controls and shutdown.
- A domain with HTTPS and background services that survive SSH disconnection.

Run commands in the location marked above each block. Replace `YOUR_VPS_IP` and
`dash.example.com` with your server's IP address and dashboard domain. The Linux
account used throughout is `project15`; its project folder must be
`/home/project15/Project15` for the supplied service files to work as written.

**This document prepares a fresh runtime, not an automatic migration of the
currently running bot.** Keep the new server's live automation disabled until
Section 11. Do not run two live executors for the same account simultaneously.

## 1. Choose the server and prepare access

In IONOS, create the VPS with **Ubuntu 24.04 LTS**, and note its public IPv4 address.
Have these ready:

- SSH access to the server, initially using the administrator account IONOS provides.
- Your domain's DNS management access, if using a public dashboard.
- Your Kalshi API key ID and matching private key file.
- The latest Project15 working directory, including the new cloud/viewer files.

A domain is separate from the bot. You can complete private setup without one.

**Current computer — Linux/WSL terminal:**

If you do not already have an SSH key, create one. Do not overwrite an existing key:

```bash
ssh-keygen -t ed25519
```

Connect using the initial administrator account; this example assumes `root`:

```bash
ssh root@YOUR_VPS_IP
```

**VPS — initial administrator session:**

```bash
apt update
apt install -y sudo openssh-server python3.12 python3.12-venv curl ca-certificates rsync ufw nano gnupg
adduser project15
usermod -aG sudo project15
```

If your initial account is not root, prefix these commands with `sudo`.

Install your computer's **public** SSH key in
`/home/project15/.ssh/authorized_keys`. One approach is to copy the public key's
single line from your computer and paste it in the editor:

```bash
install -d -m 700 -o project15 -g project15 /home/project15/.ssh
nano /home/project15/.ssh/authorized_keys
chown project15:project15 /home/project15/.ssh/authorized_keys
chmod 600 /home/project15/.ssh/authorized_keys
```

Keep the administrator session open while testing the new login in another terminal.

**Current computer:**

```bash
ssh project15@YOUR_VPS_IP
```

Use this account for subsequent VPS commands. Do not use `sudo systemctl --user`:
that addresses the wrong user service manager.

## 2. Connect with VS Code

On your computer, install VS Code's **Remote – SSH** extension. Run
**Remote-SSH: Connect to Host**, choose `project15@YOUR_VPS_IP`, and connect.
After copying the project in Section 4, open `/home/project15/Project15`.

The integrated terminal in that remote VS Code window runs commands **on the VPS**.
A separate local terminal is still needed for file uploads and SSH tunnels.

## 3. Set the firewall

Allow these inbound TCP ports in the IONOS firewall attached to the server:

| Port | Purpose | Who needs access |
| --- | --- | --- |
| 22 | SSH / VS Code | You; restrict to your IP if it is stable |
| 80 | HTTP redirect and certificate validation | Public |
| 443 | HTTPS visitor dashboard | Public |

Do not allow public access to **8000**, **8001**, or Caddy's administration port
**2019**. Apply equivalent restrictions to IPv6 if you use it. Leave outbound
connectivity available for exchange feeds, APIs, DNS and software downloads.

**VPS — project15 account:**

These commands assume a fresh server using the default SSH port 22. Allow SSH
before enabling the firewall:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

Test a second SSH connection before closing the first one.

## 4. Upload the current code

**VPS:**

```bash
mkdir -p ~/Project15
```

**Current computer — terminal in the existing project:**

This copies the current source, including uncommitted additions. It deliberately
lists the directories to transfer so it does not upload the existing `data/`,
`.env`, virtual environment or Git history. Store private key files separately.

```bash
cd /home/devk/Project15
rsync -av --exclude='__pycache__/' --exclude='*.pem' --exclude='*.key' --exclude='.env*' \
  src config deploy scripts docs pyproject.toml uv.lock README.md AGENTS.md \
  project15@YOUR_VPS_IP:~/Project15/
```

If your local folder is elsewhere, change the `cd` path. A clone of an older commit
will not include the visitor dashboard or cloud changes unless those changes have
been committed and pushed first.

## 5. Install dependencies and prepare the runtime

**VPS:**

Install `uv` using its official installer, then synchronize the production dependencies.
The installer command is documented by [Astral](https://docs.astral.sh/uv/getting-started/installation/).

```bash
curl -LsSf https://astral.sh/uv/install.sh -o /tmp/project15-uv-install.sh
sh /tmp/project15-uv-install.sh
export PATH="$HOME/.local/bin:$PATH"
cd ~/Project15
uv sync --locked --no-default-groups --python 3.12
.venv/bin/python scripts/prepare_cloud.py
```

The last command creates `data/cloud/manifest.json` and four frozen strategy
configurations. It refuses to overwrite an existing `data/cloud` directory.
If that directory exists, determine whether this server is already prepared;
**do not delete a runtime containing trade/order history to rerun this command**.

This cloud profile runs no paper simulation workers and records no raw replay tapes.
It still records live orders, trade evidence and signal state. Historical research
data from your old computer is not imported. Runtime files will continue growing.

Verify the frozen entry settings:

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
for asset in ('BTC', 'ETH', 'SOL', 'XRP'):
    cfg = json.loads((Path('data/cloud') / f'{asset}.json').read_text())
    print(asset, {key: cfg[key] for key in (
        'standard_component_min_probability', 'late_component_min_probability',
        'min_probability', 'late_min_probability', 'entry_window_start',
    )})
PY
```

For the presets prepared during this work, expect Bleep floors `0.78`, blended
floors `0.0`, and an entry-window start of `420` seconds. If the presets have since
changed, review the printed values before enabling trading. Editing a saved preset
later does not update these frozen runtime files or their configuration versions.

## 6. Install the Kalshi credentials

**VPS:**

```bash
install -d -m 700 ~/.config/project15
```

**Current computer:**

Use the actual path to your existing private key:

```bash
scp /path/to/your/kalshi-private-key.pem project15@YOUR_VPS_IP:/home/project15/.config/project15/kalshi.pem
```

**VPS:**

```bash
chmod 600 ~/.config/project15/kalshi.pem
cd ~/Project15
umask 077
nano data/cloud/credentials.env
```

Enter these two lines with your real API key ID:

```dotenv
KALSHI_API_KEY_ID=YOUR_KALSHI_API_KEY_ID
KALSHI_PRIVATE_KEY_PATH=/home/project15/.config/project15/kalshi.pem
```

Then:

```bash
chmod 600 data/cloud/credentials.env
```

Do not paste the key or its contents into chat or place it in the public site's
files. The public viewer does not need Kalshi credentials.

The service files intentionally retain `TRADING_MODE=PAPER` and
`ENABLE_LIVE_TRADING=false`. Those flags guard the legacy strategy executor.
**The separate live executor can still place real orders when its dashboard
policies are enabled.** Do not change these flags to enable the cloud bot.

## 7. Install and start the background services

**VPS:**

```bash
cd ~/Project15
mkdir -p ~/.config/systemd/user
cp deploy/cloud/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
sudo loginctl enable-linger project15
systemctl --user enable --now project15-signal@BTC project15-signal@ETH project15-signal@SOL project15-signal@XRP
systemctl --user enable --now project15-execution project15-dashboard
```

Collectors may need time to populate their databases and warm up. The dashboard
may restart during initial startup if those databases are not ready yet.

```bash
systemctl --user status project15-signal@BTC project15-signal@ETH project15-signal@SOL project15-signal@XRP project15-execution project15-dashboard --no-pager
journalctl --user -u project15-execution -n 50 --no-pager
journalctl --user -u project15-signal@BTC -n 50 --no-pager
```

These services restart after failures with a 15-second delay. Lingering allows
them to run after logout and start at boot. Closing VS Code or SSH does not stop
them. A clean shutdown is not automatically restarted by `Restart=on-failure`.
See [systemd's restart rules](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html#Restart=).

## 8. Open the private dashboard

**Current computer — leave this terminal running:**

```bash
ssh -N -L 18000:127.0.0.1:8000 project15@YOUR_VPS_IP
```

Open **http://127.0.0.1:18000** on your computer. Local port 18000 avoids conflicting
with your existing local dashboard on port 8000.

Alternatively, use VS Code's Ports panel to forward remote port 8000 privately.
Do not publish or share that forwarded control port.

Verify all four markets appear, feeds become fresh, and live automation is still
disabled. **Do not enable trading yet.** The existing machine may still be trading.

## 9. Set the shutdown passcode and start the visitor site

**VPS:**

```bash
cd ~/Project15
.venv/bin/python scripts/set_public_shutdown_passcode.py
systemctl --user enable --now project15-public-dashboard
```

The script asks you to enter and confirm a unique **16–256-character** passcode.
Use a password-manager-generated value or a strong passphrase. Input is hidden.
It stores a salted hash in `data/cloud/public-shutdown.json`, not the passcode.
Keep the passcode in your password manager; do not put it in a URL.

If the viewer was already running, reload the secret by restarting only that service:

```bash
systemctl --user restart project15-public-dashboard
```

Check the local visitor endpoints:

```bash
curl -fsS http://127.0.0.1:8001/api/view
curl -fsS http://127.0.0.1:8001/api/stop
```

The second response should contain `"enabled":true` and initially `"status":"idle"`.
If the passcode file is absent, shutdown stays disabled and its form is hidden.

## 10. Connect your domain and enable HTTPS

### DNS

At your domain's DNS provider, create:

| Type | Name | Value |
| --- | --- | --- |
| A | `dash` | Your VPS public IPv4 address |

This gives you `dash.example.com` if your domain is `example.com`. Do not add an
AAAA record unless IPv6 is correctly configured for this same server. Remove or
correct conflicting records for this hostname. Allow time for DNS to update.

### Install Caddy

Caddy receives public web traffic and forwards it to the visitor dashboard.
Use its [official Ubuntu package instructions](https://caddyserver.com/docs/install#debian-ubuntu-raspbian).
On this fresh server:

**VPS:**

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
sudo chmod o+r /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
sudo nano /etc/caddy/Caddyfile
```

Replace the default configuration on this fresh server with the following,
substituting your real domain:

```caddyfile
 dash.example.com {
     reverse_proxy 127.0.0.1:8001
 }
```

**Use port 8001, never 8000.** Do not add any route that forwards to the private
control dashboard. The complete public site, including its API paths, goes to
8001. No static file directory needs to be shared from your home folder.

```bash
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl enable --now caddy
sudo systemctl reload caddy
```

With working DNS and ports 80/443 reachable, Caddy obtains and renews the TLS
certificate and redirects HTTP to HTTPS. See [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https).

Open **https://dash.example.com**. Verify HTTPS works before entering the shutdown
passcode. Visitors can see your market status and recorded trading results.
Only someone with a valid one-minute unlock can change live buy settings, edit
contract quantity, or request shutdown.

**Current computer — public boundary checks:**

```bash
curl -I https://dash.example.com/
curl -i https://dash.example.com/api/strategy
curl -i -X POST https://dash.example.com/api/strategy
```

The private strategy GET should return **404**; the POST should return **405**.
If you see strategy settings or an editable control page, stop publishing the
site and correct the reverse proxy destination before continuing.

**VPS — check bind addresses:**

```bash
ss -ltn | rg ':8000|:8001'
```

If `rg` is unavailable, use `ss -ltn` and inspect the output. Both dashboards
should listen on **127.0.0.1**, not `0.0.0.0` or a public address.

## 11. Verify the setup, then switch live trading to the VPS

Before enabling any live policy:

1. Check fresh market data and healthy recovery for BTC, ETH, SOL and XRP.
2. Verify the intended frozen configuration and contract quantity in the private dashboard.
3. While the VPS has no live positions and its automation is disabled, test the
   visitor owner panel: enter the passcode, click **Unlock for 1 minute**, then
   use the shutdown confirmation checkbox and button before the minute expires.
   Wait for a positive shutdown confirmation.
4. Start the collectors and private dashboard again:

   ```bash
   systemctl --user start project15-signal@BTC project15-signal@ETH project15-signal@SOL project15-signal@XRP project15-execution project15-dashboard
   systemctl --user restart project15-public-dashboard
   ```

5. On the **old machine**, finish/reconcile all managed orders and positions, use
   its safe shutdown, and ensure its executor cannot resume placing orders.
   Check its startup services too; do not rely on closing a terminal. Verify the
   final account state in Kalshi.
6. Only then enable each desired live market through the **VPS private dashboard**.
7. Observe the first entries, fills, exit orders and market rollover. Reconcile
   the recorded results against Kalshi before leaving it unattended.

The new runtime has a fresh local history and risk state. If you need to retain
existing positions, order ownership or cumulative risk state, use a separately
reviewed journal/configuration migration instead of these fresh-start instructions.
Never delete a ledger or reset a runtime to bypass ownership or recovery checks.

You can inspect collector health without changing settings:

```bash
cd ~/Project15
for asset in BTC ETH SOL XRP; do
  .venv/bin/btc15 --data-dir "data/cloud/$asset" paper-health --run-id "$asset-live-signals"
done
```

The command's historical name is `paper-health`; it also supports these live signal
collectors. A service showing `active` alone does not establish healthy trading.

## 12. Daily use and maintenance

### Visit and control

- **Public viewing:** `https://dash.example.com`.
- **Private controls:** SSH tunnel, then `http://127.0.0.1:18000`.
- **Editing files:** VS Code Remote SSH, folder `/home/project15/Project15`.
- **Heavy backtests:** run on your local computer so they do not compete with live bots.

### What the shutdown button does

The passcode and checkbox authorize the existing safe shutdown workflow for all
four markets. It does **not** sell contracts or force a stop while a live position
or unresolved order still needs management. When allowed, it disables live entries
and stops the collectors/private dashboard cooperatively. The order executor
process may remain running with automation disabled.

An unknown outcome is not confirmation: inspect the private dashboard or service
logs. Do not independently stop collectors while an open position depends on them
for exit monitoring. There is no public service-restart endpoint or strategy editor. Owner-authorized
live policy edits are described below.

After a confirmed shutdown, use the start commands in Section 11, verify health,
and re-enable the desired live policies privately. Restarting the visitor resets
its in-memory shutdown status. After a VPS reboot, enabled services start again;
policies retain their saved enablement, including being disabled by safe shutdown.

### Edit live trading and contract quantity

On the HTTPS visitor site:

1. Enter the owner passcode and click **Unlock for 1 minute**.
2. Select BTC, ETH, SOL or XRP. Review the saved settings shown.
3. Enable/disable new live buys and choose **1–20 whole contracts per entry**.
4. Check the real-money confirmation and click **Save live settings**.

Each save applies to that asset and its future markets. Disabling new buys keeps
automatic exits running. Editing quantity does not resize positions already held.
Enabling buys can lead to real orders as soon as existing strategy/risk checks pass.
The same unlock allows shutdown with its separate confirmation checkbox.

**All write access expires after 60 seconds.** Activity does not extend the unlock;
enter the passcode again after expiry. The passcode itself does not change every
minute. Expiry leaves previously saved settings in effect and does not cancel an
already accepted action. Reloading the page loses its token; restarting the viewer
invalidates all tokens. Neither the passcode nor token is stored in browser storage.

If settings changed elsewhere or the market rolled over, refresh and review before
saving again. For an uncertain save response, inspect the saved values before
retrying. You cannot enable entries through a stopped or unavailable executor.

### Change the passcode

**VPS:**

```bash
cd ~/Project15
.venv/bin/python scripts/set_public_shutdown_passcode.py
systemctl --user restart project15-public-dashboard
```

The new passcode replaces the old one. Unlock attempts are limited globally to
five per minute per viewer process; wait a minute if rate-limited.

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
storage. Restart as described in Section 11 when maintenance is complete.
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

Run these commands **on the VPS as `project15`**. Test and push the intended commit
from your local computer first. Deploy a reviewed commit, not an unreviewed moving
branch. Do not run test/replay workloads against production databases.

### Git prerequisite

The initial upload in Section 4 uses `rsync` and does not create a Git checkout.
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
configuration, validate and reload it using the commands in Section 10.

If the new release fails, keep automation disabled. Reverting code may also require
compatible dependencies and a reviewed state migration. Do not blindly restore an
old order journal: orders or fills recorded after that backup must be reconciled.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `Failed to connect to bus` | Log in directly over SSH as `project15`; use `systemctl --user` without sudo; verify lingering is enabled. |
| Collector keeps restarting | Read its journal; check key path/permissions, frozen configuration, network access and disk space. |
| Public site returns 502 | Check `project15-public-dashboard`, then `curl http://127.0.0.1:8001/api/stop`; verify Caddy targets 8001. |
| Visitor shows stale data or 503 | Check the private dashboard, collectors and execution service; allow initial warmup. Staleness is expected after safe shutdown. |
| HTTPS certificate fails | Check DNS, including any AAAA record, both firewalls, and Caddy's journal. |
| Shutdown form is missing | Check passcode setup, the updated viewer service environment, and restart the viewer. |
| Shutdown returns 409 | A position/order still needs management. Leave its existing exit management running and retry when resolved. |
| Unlock returns 429 | Wait a minute; repeated attempts share one rate limit. |
| Unlock returns 403 with the right code | Use the HTTPS site's own form; check whether the passcode was rotated and the service restarted. Ensure the proxy preserves the public host and forwarded scheme. |
| A save or shutdown returns 401 | The one-minute unlock expired. Enter the passcode again; no expired request is forwarded. |
| Bots run but do not trade | Check live policies, collector health, entry filters and risk limits in the private dashboard. |

For implementation details, see [Cloud runtime reference](CLOUD.md).
