# Managed paper operation — Linux/WSL

One Settlement Edge engine can run without an attached terminal using the existing `paper-service` command and optional systemd user-service template. This is operational validation infrastructure, not evidence of multi-day reliability or profitable trading. Native Windows does not support this command's POSIX signal-handler path; use Linux/WSL for managed service operation.

Complete [Getting started](GETTING_STARTED.md) first. For an existing database, finish [legacy retirement](SINGLE_STRATEGY.md) before switching code. Do not create a fresh database to evade unresolved inventory or daily limits.

## Choose one storage/configuration pair

From the repository root on Linux/WSL, after deciding that this is a genuinely separate new experiment:

```bash
export TRADING_MODE=PAPER
export ENABLE_LIVE_TRADING=false
export DATA_DIR="$PWD/data/production-paper"
export DATABASE_URL="sqlite:///$PWD/data/production-paper/paper.db"
uv run --locked btc15 --config data/runtime/settlement-original.json init-db
uv run --locked btc15 --config data/runtime/settlement-original.json paper-service --run-id production-paper --min-free-gb 10 --seconds 1800
```

The original frozen file is created in the setup guide. An absolute SQLite URL contains the extra slash for an absolute Unix path, as produced above. Do not point this at another existing portfolio blindly. Omit `--seconds` for continuous foreground operation. Resume repeats the same run/configuration/storage; incompatible config/mode, unresolved archived exposure and occupied writer leases are rejected.

In another terminal, repeat the environment exports (or use the same explicit absolute database override), then:

```bash
uv run --locked btc15 --config data/runtime/settlement-original.json dashboard --no-collect --port 8001
uv run --locked btc15 paper-health --run-id production-paper
```

Open `http://127.0.0.1:8001` and choose PAPER / `production-paper`. **A viewer using default `data/btc15.db` will not show this service's database.** The viewer's `DATA_DIR` also determines where Settings writes and HALT commands target. Always keep the pair consistent.

## Stop and health semantics

SIGINT/SIGTERM and the coordinated dashboard shutdown stop entries, cancel unfilled remainders, drain/flush inputs and save filled positions. They do not liquidate inventory or invent fills during downtime. A named run with no trades still receives an initial checkpoint for clean restart.

The default disk reserve is 10 GiB on the data filesystem; falling below it stops operation. It does not monitor storage on a remote database server. Recordings are never auto-deleted. Monitor actual tape/database growth and preserve backups before disk pressure.

`paper-health` exits 0 only when status is recent, PAPER execution/connection/clock/exchange state are healthy, no halt is active, reference age is at most two seconds and processing lag at most one second. It is a stricter operating probe, not a signal or fill guarantee. During warmup/degradation it can exit 1. External alert delivery is not configured.

## Optional systemd user service

The shipped [unit](../deploy/btc15-paper.service) assumes `$HOME/Project15`, its `.venv`, and `data/production-paper/paper.db`. It sets PAPER, disables live trading, uses WARNING console logging, allows 60 seconds to stop and sets `Restart=no`. It is a template, not an installed service. Do not enable it while a foreground writer owns that database.

```bash
mkdir -p ~/.config/systemd/user
cp deploy/btc15-paper.service ~/.config/systemd/user/btc15-paper.service
```

Review the copied unit before starting. Correct all paths for your checkout and explicitly pin its configuration. For the setup guide's original frozen file, change the copied unit's `ExecStart` to:

```ini
ExecStart=%h/Project15/.venv/bin/btc15 --config %h/Project15/data/runtime/settlement-original.json paper-service --run-id production-paper --min-free-gb 10
```

Without this edit the template loads settings using CLI precedence; a different `DATA_DIR/strategy.json` or later saved settings can cause a resume mismatch. Keep credentials protected in the expected local environment/key file; a service can have a different environment from an interactive shell.

After checking paths/configuration and confirming no other writer:

```bash
systemctl --user daemon-reload
systemctl --user start btc15-paper
systemctl --user status btc15-paper
journalctl --user -u btc15-paper -f
```

Only after a successful manual service test, `systemctl --user enable btc15-paper` enables it for the user-service lifecycle. Stop with `systemctl --user stop btc15-paper`. A clean restart uses the same original config/run; fatal errors require inspection because automatic restart is disabled. See [systemd user-service guidance](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html).

Beyond logout, user-service lifetime/lingering must be configured separately by the operator. WSL and its Windows host must remain running/awake. This repository does not provision Windows startup, sleep policy or user lingering. Enabling a service is not equivalent to an always-on cloud server.

## Recovery and acceptance

Never steal a writer lease. A killed process or power loss requires [crash recovery](SAFETY.md#crash-recovery), not a blind restart. HALT remains latched in checkpoints; removing its file alone does not unhalt a resumed run.

Prior two-session/78-test results in the [earlier service report](https://github.com/KhoaDao03/Project15/blob/c23981dd0475d10af24caf1dde372d9477e78e08/docs/AUTONOMOUS_PAPER.md) describe an earlier host/revision, not this installation. Current software evidence is in [Validation](VALIDATION.md). Verify repeated rollovers, official settlements, UTC daily limits, interruptions with inventory, actual crash recovery, resource growth and operator detection of failures during consecutive real-data sessions. Do not use a short smoke run as multi-day acceptance.
