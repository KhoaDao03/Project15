# Managed autonomous PAPER operation

The service combines authenticated collection, model evaluation, simulated orders,
position/risk accounting and official settlement without a terminal session.
Implementation is available for operational validation. Multi-day acceptance has
not been completed; software operation does not establish strategy performance.

## Run and monitor

Use an isolated database and data directory. Keep existing credentials in the
project `.env`; the service uses the same authenticated, read-only API client.

```bash
cd /home/devk/Project15
export TRADING_MODE=PAPER ENABLE_LIVE_TRADING=false
export DATA_DIR=data/production-paper
export DATABASE_URL=sqlite:///data/production-paper/paper.db
.venv/bin/btc15 paper-service --run-id production-paper
# Another terminal, with the same environment:
.venv/bin/btc15 paper-health --run-id production-paper
```

The command starts a new named run or resumes its checkpoint, including a run
that has not traded. Collection/BACKTEST identities, configuration mismatches and
occupied writer leases are rejected. Resume cancels resting remainders, retains
positions and daily limits, and starts with unhealthy feeds. No fills are assigned
to downtime. Keep the same configuration on restart; pass global `--config PATH`
before the command to pin a frozen configuration. Without it, CLI startup reads
`DATA_DIR/strategy.json` when present, including settings saved from a dashboard
using the same data directory. A dashboard using another `DATA_DIR` edits another
file. UI saves do not reconfigure a running service. Before restarting a named
run, ensure its configuration still matches the checkpoint; see
[strategy configuration precedence](STRATEGY.md#configuration-precedence-and-reproducibility).

`--seconds N` bounds a validation session. Otherwise it runs continuously.
SIGINT/SIGTERM stop producers, drain received frames, record disconnection, cancel
pending entries through normal disconnection handling, flush raw data and release
the writer lease. Filled positions remain checkpointed for the next start.

`--min-free-gb` defaults to 10 GiB. Falling below this data-filesystem reserve stops
the process with a failure; recordings are never automatically deleted. The reserve
does not monitor remote database storage. Provision/archive recordings before the
disk fills. Record actual daily storage and memory growth during acceptance.

The health command returns JSON and exit 0 only for status no older than five
seconds, connected executing PAPER, healthy clock, open exchange, no halt,
reference age at most two seconds and processing lag at most one second. These
probe thresholds are independent of strategy entry thresholds. Exit 1 means
missing, stale or degraded status. This is not a trade signal or proof of book
liquidity. Logs/probes are local; external alert delivery is not configured.

## User service

The definition assumes `$HOME/Project15` and an installed `.venv`. It fixes PAPER
mode and uses a separate production-paper database. Review paths on other hosts.

```bash
mkdir -p ~/.config/systemd/user
cp deploy/btc15-paper.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now btc15-paper
systemctl --user status btc15-paper
journalctl --user -u btc15-paper -f
systemctl --user stop btc15-paper
```

Use the same DATA_DIR/DATABASE_URL above for health and halt commands.
The service allows 60 seconds to stop and does not automatically restart failures
([systemd service semantics](https://github.com/systemd/systemd/blob/main/man/systemd.service.xml)).
Network interruptions reconnect within the collector. Fatal authentication, disk,
database or process failures require investigation. SIGKILL/power loss leaves the
writer lease intact; follow [recovery instructions](SAFETY.md) before restarting.
No timeout steals a possibly active writer's lease. A clean `systemctl --user
restart btc15-paper` resumes the same run.

Beyond logout, the host must keep the user service manager running (user lingering).
WSL must remain running and Windows awake. This change does not configure Windows
startup, suspend policy or lingering. The definition is supplied, not automatically
installed/enabled.

`btc15 halt` blocks entries while allowing position/settlement handling. The
checkpoint retains this halt across managed restarts; deleting HALT alone does not
clear it. Do not edit a checkpoint to bypass it. Review the run and resolve exposure
before starting a new named run.

## Validation and remaining acceptance

- Full regression suite: **78 passed**, two existing third-party deprecation warnings.
- New tests cover clean named-run restart, initial checkpoints without fills,
  stop/drain behavior, low disk, occupied crash leases and stale/degraded health.
- Ruff lint/format, whitespace and host systemd unit validation passed.
- Two authenticated PAPER sessions used the same `operation-acceptance` run. The
  first completed its 30-second limit; the second resumed, reported healthy after
  20 seconds and exited 0 on SIGTERM. Execution was enabled, but no simulated fills
  occurred. Live health reported 0.002 s processing lag and 0.234 s reference age.
  Evidence is retained in `data/operation-smoke/`.

Before production readiness, run consecutive multi-day sessions with repeated
rollover, settlement and daily-limit rollover; test interruptions with outstanding
simulated positions and an actual killed process; measure resource growth and
audit all capture segments. Verify operator detection of failed service. Evaluate
calibration and pessimistic fill/cost assumptions on independent held-out data as
a separate research gate. Short smoke tests do not close these gates. LIVE remains
blocked.
