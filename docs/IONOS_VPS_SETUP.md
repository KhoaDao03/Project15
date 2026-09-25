# VPS setup

This guide prepares a fresh Ubuntu 24.04 host. Use [cloud deployment](CLOUD.md)
for runtime credentials/services and [maintenance](VPS_MAINTENANCE.md) for updates.
For the existing fleet, see [deployment status](RESEARCH_LOGGING.md#deployment-status).

## Which account should I use?

Use the account that owns `~/Project15` for all bot services:

| Project | Account |
| --- | --- |
| `/root/Project15` | `root` |
| `/home/project15/Project15` | `project15` |

An existing root installation does not need an account change. A dedicated account
is preferable for a fresh host. Run `systemctl --user` as that account, without
`sudo`; user services resolve the project relative to its home directory.
Replace `YOUR_USER`, `YOUR_VPS_IP` and the example domain below.

## Already installed? Start here

On the VPS, verify the existing runtime before starting anything:

```bash
cd ~/Project15
ls data/cloud/manifest.json data/cloud/credentials.env
systemctl --user status 'project15-signal@*' project15-execution project15-dashboard --no-pager
```

Use the [service commands](CLOUD.md#prepare-a-fresh-server) for an approved start,
or [maintenance](VPS_MAINTENANCE.md#deploy-changes-and-restart-the-affected-services)
for a code update. Do not rerun runtime preparation or overwrite frozen configs.
A start does not restart an already-running service. Existing saved policies may
resume real trading; a fresh installation starts with buys disabled.

From your computer, leave this private-dashboard tunnel open:

```bash
ssh -N -L 18000:127.0.0.1:8000 YOUR_USER@YOUR_VPS_IP
```

Visit `http://127.0.0.1:18000`. Closing the tunnel does not stop services.

## First-time setup

### 1. Prepare the account

Connect as root, then on the VPS:

```bash
apt update
apt install -y sudo openssh-server python3.12 python3.12-venv curl ca-certificates rsync ufw nano gnupg
adduser project15
usermod -aG sudo project15
install -d -m 700 -o project15 -g project15 /home/project15/.ssh
nano /home/project15/.ssh/authorized_keys
```

Paste your computer's public SSH key, not a private key. Save, then:

```bash
chown project15:project15 /home/project15/.ssh/authorized_keys
chmod 600 /home/project15/.ssh/authorized_keys
```

Keep the root session open while testing `ssh project15@YOUR_VPS_IP` in another
terminal. Use the new account for the remaining project commands.

### 2. Set the firewall

Allow TCP 22 in IONOS; add 80/443 only for the optional public website. Keep
8000, 8001 and 2019 private, including over IPv6. For a fresh host using SSH port 22:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw enable
sudo ufw status
```

Verify another SSH login before closing the existing one.

### 3. Transfer reviewed code

Create `~/Project15` on the VPS. From a Linux/WSL terminal in your local checkout:

```bash
rsync -av \
  --exclude='__pycache__/' --exclude='*.pem' --exclude='*.key' \
  --exclude='.env*' --exclude='*.env' \
  src config deploy scripts docs pyproject.toml uv.lock README.md AGENTS.md \
  YOUR_USER@YOUR_VPS_IP:~/Project15/
```

This includes local source edits but not data, the virtual environment or Git
history. Skip transfer if reviewed code is already present. Never copy secrets
with source or clone over an existing runtime.

### 4. Install uv and prepare the runtime

On the VPS, install uv if needed:

```bash
curl -LsSf https://astral.sh/uv/install.sh -o /tmp/project15-uv-install.sh
sh /tmp/project15-uv-install.sh
export PATH="$HOME/.local/bin:$PATH"
```

Follow [cloud preparation](CLOUD.md#prepare-a-fresh-server) for dependencies,
exclusive runtime creation, credentials and service installation. It creates
four crypto configs; commodity installation is described [separately](COMMODITIES.md).
Never delete an existing `data/cloud` to make preparation succeed.

Store the private key separately, for example under `~/.config/project15/`
(directory mode 700, key mode 600). Transfer only that key through a private `scp`
command and use its absolute path in `data/cloud/credentials.env`. Protect that
file with mode 600. The services do not use a root-level credential file.

## Before enabling real trading

Require fresh feeds, completed warm-up, healthy recovery and fresh decisions for
each configured asset; a service marked `active` is insufficient. Review frozen
settings and quantity, then explicitly confirm desired live policies. Monitor
initial fills/exits and rollover, reconciling against the exchange.

Moving hosts additionally requires reconciled exposure and proof that the old
executor cannot resume trading. This fresh setup is not a migration of existing
positions, order journals or risk state.

## Optional public website

For the current `public-web` deployment, follow [public website isolation](PUBLIC_WEBSITE_ISOLATION.md)
instead of the legacy port-8001/passcode setup below. Caddy connects to a Unix
socket; owner controls remain private through SSH.

The site publishes trading results. First start the viewer and configure any owner
passcode using [cloud instructions](CLOUD.md#optional-public-view-only-dashboard).
Use HTTPS before entering the passcode.

Point a DNS A record such as `dash.example.com` at the VPS. Add AAAA only when IPv6
works. Allow 80/443 in both IONOS and the host firewall:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
```

Install Caddy using its [package instructions](https://caddyserver.com/docs/install#debian-ubuntu-raspbian).
The supplied [Caddy example](../deploy/cloud/Caddyfile.example) proxies the public
app. On a fresh site use your actual domain:

```caddyfile
dash.example.com {
    reverse_proxy 127.0.0.1:8001
}
```

Preserve any other existing sites. **Proxy to 8001, never private port 8000.**

```bash
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl enable --now caddy
sudo systemctl reload caddy
```

Once DNS/firewalls permit HTTPS, verify from your computer:

```bash
curl -i https://dash.example.com/api/strategy
curl -i -X POST https://dash.example.com/api/strategy
```

Expect 404 for GET and 405 for POST. On the VPS, `ss -ltn` should show app ports
8000/8001 bound to loopback. Fix exposure before using owner controls.

## Everyday use

Use the tunnel for private controls. For a planned stop, request safe shutdown and
wait for acknowledgment: it does not liquidate positions. Managed exposure or unknown
orders can block it. A stopped collector requires a service start; public controls
cannot start services. After a confirmed stop/start, verify health and re-enable
only intended asset policies. See [maintenance](VPS_MAINTENANCE.md) for backups,
passcode rotation, updates and errors.
