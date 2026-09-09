# Authenticated collection recovery — 2026-09-09 UTC

This follow-up addresses the clock instability and sustained rollover/recovery
validation requested after the initial hardening review. Historical failed captures
are preserved; they are not relabeled or timestamp-corrected.

## Clock root cause and actual host change

Kernel tracing identified **two controllers adjusting the same WSL clock**:
Ubuntu's `systemd-timesyncd` and `chronyd` in WSL's hidden system distribution.
The latter is outside Ubuntu's process namespace, which is why an ordinary Ubuntu
process listing did not reveal it.

The clock tick was around 10,336–10,362 microseconds rather than the normal 10,000:
roughly a 3.4% rate correction. Tracing `do_adjtimex` showed chronyd writing those
tick values while timesyncd stepped the wall clock backward approximately once per
32 seconds. A manual tick reset was overwritten. Trials of the Hyper-V TSC-page,
Hyper-V MSR and ACPI clock sources did not fix the competing controllers.

The persistent repair on **this Ubuntu WSL installation** was:

```bash
sudo systemctl disable --now systemd-timesyncd
```

WSL's system-distribution chronyd remains active and follows its Hyper-V PHC host
reference. The original `tsc` clock source was restored. The tick converged to
10,000 microseconds, frequency correction to approximately 1 ppm or less, and the
repeated backward steps stopped. No clock-source override, cron task, custom boot
unit, or periodic clock-reset script was installed. The service-disable setting
persists; a full WSL reboot was not performed during the capture.

This repair is specific to a WSL environment with an active system-distribution
clock controller. The same conflict is explicitly handled by the
[NixOS-WSL implementation](https://github.com/nix-community/NixOS-WSL/blob/main/modules/wsl-distro.nix).

Check the intended state from WSL:

```bash
systemctl is-enabled systemd-timesyncd  # disabled
systemctl is-active systemd-timesyncd   # inactive
wsl.exe --system --cd / -- chronyc tracking
cat /sys/devices/system/clocksource/clocksource0/current_clocksource  # tsc
```

`timedatectl` can report Ubuntu's NTP service inactive while WSL's controller is
synchronizing the shared clock. Diagnose the actual controller and recorded clock
behavior rather than using that one service flag as the readiness test.

For a configuration rollback, `sudo systemctl enable --now systemd-timesyncd`
restores the previous Ubuntu service state. Doing so while WSL chronyd still
controls the same clock recreates the conflict; choose one clock controller.

**Accuracy distinction:** chronyd's microsecond-scale offset is relative to the
Windows/Hyper-V host reference. Independent queries to Ubuntu NTP and Cloudflare
measured the host about 0.62 seconds ahead of UTC, within the unchanged two-second
application skew limit. This is not a sub-millisecond UTC accuracy claim. Windows
Time service configuration was not changed.

Local diagnostic artifacts: `data/collection-recovery/clock-adjustments.txt`,
`clock-probe*.json`, `single-controller-clock.json`, and
`external-time-check.json`. Temporary tracing instances/probes were removed.

## Rollover and recovery fixes

- Keep the authenticated connection and both BRTI subscriptions alive during
  normal rollover. Add/remove markets on book, trade and ticker subscriptions.
- Send one subscription ID per update request. The production API rejected a
  multi-ID request with “Exactly one subscription ID is required”; the authenticated
  single-ID probe received acknowledgements on all three market channels while
  reference delivery continued. See the
  [WebSocket command reference](https://docs.kalshi.com/websockets/websocket-connection).
- Keep subscribed books warm while a market's strike is unpublished, including
  briefly active markets with pending strike metadata. Activation preserves the
  existing snapshot and sequence instead of requiring a reference reconnect.
- Retain the earlier empty-snapshot fix: omitted level arrays clear stale quotes
  and represent an empty, non-executable book.
- Transport failures still reconnect and require a fresh snapshot. A controlled
  loss test verifies separate session IDs, rebuilt depth, retained raw records
  and no false sequence errors. Actual missing packets cannot be reconstructed;
  an affected recording remains visibly incomplete.

## Qualification evidence

The final bounded capture uses `data/collection-recovery/final.db` and
`data/collection-recovery/final/raw/`. It runs with paper execution disabled and
sends no exchange orders. Its run source snapshot identifies the exact collector
code. **Qualification passed**, with the following completed results:

| Check | Result |
| --- | --- |
| Window | 2026-09-09 00:27:15–00:43:55 UTC; 1,000.458 monotonic seconds |
| Raw events | 453,202; recorded, non-synthetic |
| Socket continuity | One connection; six rollover updates, all acknowledged |
| Clock / sequence / standard-reference gaps | Zero / zero / zero |
| Largest adjacent wall/monotonic interval discrepancy | 44 microseconds |
| Standard / 5 Hz BRTI samples | 999 / 4,994 |
| Settlement window | All 60 standard samples; official NO for `KXBTC15M-26SEP082030-30` |
| Observed / published mean | 78619.64716666666 / 78619.64716667; match within publication precision |
| Processing lag | p99 0.078 s; maximum 0.243 s; maximum queue 1,010 |
| Shutdown / health | Clean; only the two expected shutdown disconnect records |
| Audit / qualification | `research_valid=true`; `qualification_passed=true` |

The audit's `connections=2` counts the initial internal event namespace as well as
the socket namespace; `counts.connected=1` is the actual socket connection count.
The shutdown disconnect records do not represent reconnects during capture.

- Run: `efb46230-c9a6-4d93-808d-d96f1f117a79`.
- Collector source SHA-256: `92aef4c4ad19d632667fdb459dd15c5f7209e9f6e9ab77f74fb9b5c1345e1f3c`.
- Journal: `final/raw/f728a4fc-7b4e-41df-91e4-cda13b18a03d.jsonl`.
- Journal SHA-256: `8a59346c772c8f8f09698d89872f6a8e20361ea0bb7f1a03792c2f8377003fb6`.
- Full metrics and acceptance script: `data/collection-recovery/final-audit.json`
  and `data/collection-recovery/qualify.py` (local, ignored artifacts).

Authenticated collection is **READY for authenticated research collection** on the
repaired host. These results close the reported clock and final-rollover gates.
Each subsequent dataset still requires audit. Multi-day unattended operation,
empirical calibration and real execution measurements are separate requirements;
autonomous production PAPER and LIVE remain NOT READY.

The full regression suite passed 72 tests after the rollover changes. A subsequent
controlled transport-loss test passed as part of the five-test collector suite.
These are software/recovery tests; the real capture provides separate authenticated
continuity and settlement evidence.

Replay of all 453,202 events also completed successfully in an isolated BACKTEST
database, with no failed experiment, zero trades and no open positions. Run
`14f2c1d3-f8df-4643-a9f7-7909e4fe11d4` retains the parent capture and matching input
hash; see `data/collection-recovery/replay-audit.json` and `replay.log`. The single
settled market does not establish calibration or economic edge.
