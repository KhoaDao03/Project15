# Public dashboard UI review

Implementation: `/root/Project15-public-ui`, branch `public-ui-review`, based on
`cloud-deploy`. The original `/root/Project15` checkout is clean and restored.

## Preview

Open http://127.0.0.1:8015/. This isolated preview uses clearly labeled synthetic
records and has no trading connections or mutation endpoints. To start it again:

```bash
cd /root/Project15-public-ui
PYTHONPATH=src /root/Project15/.venv/bin/python scripts/preview_public_ui.py
```

[Desktop screenshot](desktop.png) · [Mobile screenshot](mobile.png)

Screenshots use synthetic APIs, including mocked owner availability. Operational
interactions were tested with mocks; no production owner actions were submitted.

## Changes

- Applied the reference composition, dark green palette, supplied DK logo,
  grouped market table, selected-market detail, synchronized tabs and owner bar.
- Weighted overall win rate uses total wins divided by completed trades.
  Break-even counts come from the existing fleet metric. Missing fields remain
  unavailable and incomplete totals are labeled partial. Tied crypto leaders and
  empty history are handled explicitly.
- Preserved trade columns, YES/NO sides, opening/exit timestamps, sale versus
  settlement labels, and pending realized results for open trades.
- Added bounded read-only history at `/api/history/{asset}` (25 rows per UI page;
  API maximum 100). It reads the same asset/run source as the snapshot and filters
  every row through the public field allowlist. WTI stays the backend identifier.
- Retained server authentication, 60-second expiry, revision checks, confirmation,
  quantity limits and shutdown safeguards. Polling preserves quantity drafts;
  stale/rejected updates lock controls pending a fresh snapshot. Passcodes clear
  on submission and tokens remain only in memory.
- Tables scroll independently on narrow screens. Market selection is operable by
  keyboard; inputs are labeled and focus states are visible.

## Verification

```bash
cd /root/Project15-public-ui
PYTHONPATH=src \
PATH=/root/Project15/.venv/lib/python3.12/site-packages/playwright/driver:$PATH \
LD_LIBRARY_PATH=/tmp/dekings-browser-libs/root/usr/lib/x86_64-linux-gnu \
PROJECT15_UI_SCREENSHOTS=1 \
/root/Project15/.venv/bin/python -m pytest -q \
  tests/test_public_dashboard.py tests/test_public_ui_browser.py \
  tests/test_recent_trade_snapshot.py tests/test_fleet_dashboard.py \
  tests/test_trade_lifecycle_dashboard.py
```

Result: **71 passed**, no skips, with two existing Starlette deprecation warnings.
Rendered label, P&L, badge and primary-button text contrasts measured 7.58:1–10.03:1.

The browser scenario covers aggregate metrics, ties/empty/missing data, market
selection, history paging, drafts across polling, confirmed saves, disabled buying,
rejected revisions, failed refreshes, owner expiry, and browser-storage absence.
Desktop (1440), tablet (768), and mobile (390) layouts are checked for page overflow
and JavaScript errors. Existing API tests cover unauthorized/expired mutations,
confirmation, limits, stale data and per-market shutdown. Added API tests cover
history paging, parameter bounds, fixed upstream paths and private-field removal.
Ruff, Node syntax checking and `git diff --check` are also run. Full repository
suite and non-Chromium browsers were not tested.

## Limits and source isolation

Remote Start is unsupported by the existing backend. The button stays disabled
with an explanation; service-management endpoints were not added. A future
rollout must restart the public backend to load the history route and forwarded
break-even metric. No strategy, execution, risk, private dashboard or live setting
changes are included.

During implementation, a read-only production check revealed that the running
public service serves static files directly from `/root/Project15`. Initial edits
were therefore briefly visible without a deployment command. The complete change
was moved to this isolated worktree, the original files were restored, and an HTTP
comparison verified the public HTML matched the original checkout. No production
service was restarted and no production owner mutation was made.
