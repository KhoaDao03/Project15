# Start Project15 on an IONOS VPS

Project15 runs four signal collectors (BTC, ETH, SOL, XRP), one real-order
executor, and a private dashboard. A public website is optional.

**Already installed? Use the startup section below.** Only follow first-time setup
on a fresh server. Updates and backups are in [VPS maintenance](VPS_MAINTENANCE.md).

## Which account should I use?

Use the same Linux account for the project files and all bot services:

| Project location | SSH login | Account |
| --- | --- | --- |
| `/root/Project15` | `ssh root@YOUR_VPS_IP` | `root` |
| `/home/project15/Project15` | `ssh project15@YOUR_VPS_IP` | `project15` |

If your prompt says `root@ubuntu:~/Project15`, you are using **root**. You do not
need to create or switch to `project15` to start that existing installation.
A dedicated `project15` account is recommended for a fresh installation.

The service files use your home directory automatically. Run `systemctl --user`
without `sudo`. The linger command below uses your current account, so it works
for either installation.

In commands run **on your computer**, replace `YOUR_USER` with that account and
`YOUR_VPS_IP` with the server's IP. Replace `dash.example.com` with your domain
only if you set up the optional website.

## Already installed? Start here

**On the VPS**, logged in as the account that owns the project:

```bash
cd ~/Project15
ls data/cloud/manifest.json data/cloud/credentials.env
```

Both files must exist. If either is missing, complete the corresponding first-time
setup step below. The services use `data/cloud/credentials.env`, not a credentials
file in the project root.

Install/refresh the service definitions and start the bot:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/cloud/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
sudo loginctl enable-linger "$(id -un)"
systemctl --user enable --now \
  project15-signal@BTC project15-signal@ETH \
  project15-signal@SOL project15-signal@XRP \
  project15-execution project15-dashboard
```

This starts stopped services and enables them at boot; it does not restart services
that are already running. For software updates, use [VPS maintenance](VPS_MAINTENANCE.md).

Check status and recent errors:

```bash
systemctl --user status 'project15-signal@*' project15-execution project15-dashboard --no-pager
journalctl --user -u project15-execution -n 50 --no-pager
journalctl --user -u project15-signal@BTC -n 50 --no-pager
```

Collectors need time to warm up. The dashboard may restart while their databases
are first being created. Services restart after failures and keep running after
SSH disconnects. A clean shutdown stays stopped until started again or the user
service manager restarts.

**On your computer**, open a terminal and leave this tunnel running:

```bash
ssh -N -L 18000:127.0.0.1:8000 YOUR_USER@YOUR_VPS_IP
```

Open **http://127.0.0.1:18000** in your browser. Port 18000 is on your computer;
port 8000 is the private dashboard on the VPS. Keep server port 8000 private.

**Fresh installations start with live automation disabled. Existing installations
remember their saved live settings and may resume trading when started.** See
[Before enabling real trading](#before-enabling-real-trading).

## First-time setup

Use this section for a fresh Ubuntu 24.04 VPS. You need SSH access, the current
Project15 source, and your Kalshi API key ID and private key file. No domain is
needed for the private dashboard.

### 1. Prepare the server account

**On your computer:**

```bash
ssh root@YOUR_VPS_IP
```

**On the VPS, as root:**

```bash
apt update
apt install -y sudo openssh-server python3.12 python3.12-venv curl ca-certificates rsync ufw nano gnupg
adduser project15
usermod -aG sudo project15
install -d -m 700 -o project15 -g project15 /home/project15/.ssh
nano /home/project15/.ssh/authorized_keys
```

Paste your computer's **public SSH key** into that file, then save it. In nano,
press Ctrl+O, Enter, then Ctrl+X. If you need an SSH key, run
`ssh-keygen -t ed25519` on your computer; do not overwrite an existing key.

**On the VPS, as root:**

```bash
chown project15:project15 /home/project15/.ssh/authorized_keys
chmod 600 /home/project15/.ssh/authorized_keys
```

Keep this session open. In a second terminal **on your computer**, test:

```bash
ssh project15@YOUR_VPS_IP
```

Use that new account for all remaining VPS commands. In the remaining computer
commands, `YOUR_USER` is now `project15`.

### 2. Set the firewall

In the IONOS firewall, allow TCP **22** for SSH. For the optional public website,
also allow **80** and **443**. Keep **8000**, **8001**, and **2019** closed publicly,
including over IPv6. Leave outbound internet access available.

**On the VPS**, for a fresh server using SSH port 22:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw enable
sudo ufw status
```

Test another SSH login before closing your existing session.

### 3. Upload the code

**On the VPS:**

```bash
mkdir -p ~/Project15
```

**On your computer**, using a Linux/WSL terminal in your current Project15 folder:

```bash
rsync -av \
  --exclude='__pycache__/' --exclude='*.pem' --exclude='*.key' \
  --exclude='.env*' --exclude='*.env' \
  src config deploy scripts docs pyproject.toml uv.lock README.md AGENTS.md \
  YOUR_USER@YOUR_VPS_IP:~/Project15/
```

This uploads the listed source files, including local changes. It does not copy
`data/`, the virtual environment, or Git history. Keep private keys separate.
If the current code is already on the VPS, skip this upload.

Optional: connect VS Code Remote – SSH to the same account and open `~/Project15`.
Its remote terminal runs on the VPS, not on your computer.

### 4. Install dependencies and create the runtime

**On the VPS:**

```bash
curl -LsSf https://astral.sh/uv/install.sh -o /tmp/project15-uv-install.sh
sh /tmp/project15-uv-install.sh
export PATH="$HOME/.local/bin:$PATH"
cd ~/Project15
uv sync --locked --no-default-groups --python 3.12
.venv/bin/python scripts/prepare_cloud.py
```

Skip the installer if `uv` is already installed. The preparation script creates
`data/cloud/manifest.json` and freezes four strategy configurations. Run it only
once: it refuses an existing `data/cloud` directory. **Do not delete an existing
runtime to rerun setup.** Later source preset edits do not update those frozen files.

### 5. Add credentials

**On the VPS:**

```bash
install -d -m 700 ~/.config/project15
```

**On your computer**, replace the local key path:

```bash
scp /path/to/your/kalshi-private-key.pem YOUR_USER@YOUR_VPS_IP:~/.config/project15/kalshi.pem
```

**On the VPS:**

```bash
chmod 600 ~/.config/project15/kalshi.pem
realpath ~/.config/project15/kalshi.pem
cd ~/Project15
umask 077
nano data/cloud/credentials.env
```

Enter these two lines. Use the full path printed by `realpath` for the second value:

```dotenv
KALSHI_API_KEY_ID=YOUR_KALSHI_API_KEY_ID
KALSHI_PRIVATE_KEY_PATH=PASTE_THE_FULL_KEY_PATH_HERE
```

Save with Ctrl+O, Enter, Ctrl+X, then run:

```bash
chmod 600 data/cloud/credentials.env
```

Keep the private key out of Git and chat. Leave `TRADING_MODE=PAPER` and
`ENABLE_LIVE_TRADING=false` in the service files: these guard the legacy executor.
The separate cloud executor uses dashboard settings to enable real orders.

Now follow [Already installed? Start here](#already-installed-start-here) to
install the services and open the dashboard.

## Before enabling real trading

**On the VPS:**

```bash
cd ~/Project15
for asset in BTC ETH SOL XRP; do
  .venv/bin/btc15 --data-dir "data/cloud/$asset" paper-health --run-id "$asset-live-signals"
done
```

Despite its name, `paper-health` also checks live signal collectors.

1. Check that all four assets have fresh feeds, completed warmup, healthy recovery,
   and fresh strategy decisions. A service showing `active` is not enough.
2. Review the frozen strategy settings and intended contract quantity.
3. If moving from another machine, reconcile its orders and positions, use its
   safe shutdown, and ensure its executor cannot restart and trade. Do not run
   two live executors for the same account. This guide does not migrate existing
   positions, journals, or risk state.
4. In the private dashboard, explicitly enable and confirm each desired live asset.
5. Watch the first entries, fills, exits, and market rollover. Reconcile results
   with Kalshi before leaving it unattended.

## Optional public website

Skip this section if you only need the private dashboard. The public website
shows your trading results. Optional passcode controls allow buy settings,
contract quantity changes, and safe shutdown.

### 1. Start the public dashboard

**On the VPS**, after the main services are installed:

```bash
cd ~/Project15
systemctl --user enable --now project15-public-dashboard
curl -fsS http://127.0.0.1:8001/api/view
```

To enable owner controls, set a private 16–256-character passcode:

```bash
.venv/bin/python scripts/set_public_shutdown_passcode.py
systemctl --user restart project15-public-dashboard
```

Without a passcode file, owner controls stay disabled. The script stores a salted
hash, not the passcode. Use HTTPS before entering the passcode in a browser.

### 2. Point a domain at the VPS

At your DNS provider, create an **A record** named `dash` pointing to your VPS's
IPv4 address. For `example.com`, this gives `dash.example.com`. Only add an AAAA
record if IPv6 is configured for this server.

Allow inbound TCP **80** and **443** in the IONOS firewall, then **on the VPS**:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
```

### 3. Install Caddy for HTTPS

**On the VPS**, using the [official Caddy package source](https://caddyserver.com/docs/install#debian-ubuntu-raspbian):

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

On a fresh server, replace the default configuration with this, using your domain.
If Caddy already hosts other sites, preserve their configuration.

```caddyfile
dash.example.com {
    reverse_proxy 127.0.0.1:8001
}
```

**Always proxy to 8001. Port 8000 is the private control dashboard.**

```bash
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl enable --now caddy
sudo systemctl reload caddy
```

When DNS and the firewall are ready, Caddy sets up HTTPS. Open
**https://dash.example.com**.

Verify the public/private separation **from your computer**:

```bash
curl -i https://dash.example.com/api/strategy
curl -i -X POST https://dash.example.com/api/strategy
```

Expect **404** for GET and **405** for POST. On the VPS, use `ss -ltn` to check
that ports 8000 and 8001 listen only on `127.0.0.1`. Fix any public exposure before
using owner controls.

## Everyday use

- **Open private controls:** run the SSH tunnel above, then visit `http://127.0.0.1:18000`.
- **Close SSH or VS Code:** the bots keep running.
- **Stop the bots:** use the dashboard's safe shutdown and wait for confirmation.
  Live positions or unresolved orders block shutdown; it does not liquidate them.
  Do not stop collectors while positions still need exit monitoring. The executor
  may remain running with new entries disabled.
- **Start after safe shutdown:** use the startup commands above, check health, then
  re-enable the desired assets in the private dashboard. If using the public site,
  restart `project15-public-dashboard` to clear its old shutdown status.
- **Public owner controls:** unlock for one minute, review an asset, set new buys
  and 1–20 contracts, confirm, and save. Disabling buys preserves automatic exits;
  changing quantity does not resize held positions. Saved settings persist after
  the unlock expires. These controls cannot start stopped services.

For updates, backups, passcode changes, and error messages, use
[VPS maintenance](VPS_MAINTENANCE.md). For runtime details, see [CLOUD.md](CLOUD.md).
