# DEKINGS public dashboard review

Implementation is on the existing `cloud-deploy` branch. Only the public
application implementation changed; existing unrelated working-tree edits were
preserved. No deployment, production owner action, collector restart or trading
service restart was performed.

## Changed files

- `src/btc15/public_static/index.html`: DEKINGS shell, owner access bar, shared
  seven-card template, accessible confirmation/trade/history dialogs.
- `src/btc15/public_static/viewer.css`: navy/gold visual design, wide 4+3 grid,
  two-column and mobile layouts, focus and reduced-motion styles.
- `src/btc15/public_static/viewer.js`: keyed cards, per-asset drafts/actions,
  server-checked sessions, trade details, mode selection, history paging and
  stale/error handling. No external scripts, fonts or frontend build.
- `src/btc15/public_dashboard.py`: public-only mode/history adapter, session status,
  HttpOnly cookie access, lock-now revocation, scoped validation and owner audit
  logging. Existing safe-stop and live-control proxy operations remain intact.
- `tests/test_public_dashboard.py`, `tests/test_public_browser.py`: public API
  regressions and isolated Chromium interaction/viewport checks.
- `docs/PUBLIC_DASHBOARD.md`: complete data/action mappings, authentication and
  configuration, accounting scope, validation and approval-gated deployment notes.

`src/btc15/dashboard.py` was restored byte-for-byte to the pre-task copy. No
private UI, strategy, model, execution, accounting, data/configuration or service
unit changes are part of this implementation.

## Screenshots

All screenshots use visibly labeled synthetic fixtures and isolated authentication.
The filename without `-full` captures exactly the listed viewport; `-full` captures
the scrollable page. Both locked and unlocked versions are included.

| Viewport | Locked | Unlocked |
| --- | --- | --- |
| 1920×1080 | [Viewport](screenshots/locked-1920x1080.png) · [Full page](screenshots/locked-1920x1080-full.png) | [Viewport](screenshots/unlocked-1920x1080.png) · [Full page](screenshots/unlocked-1920x1080-full.png) |
| 1440×900 | [Viewport](screenshots/locked-1440x900.png) · [Full page](screenshots/locked-1440x900-full.png) | [Viewport](screenshots/unlocked-1440x900.png) · [Full page](screenshots/unlocked-1440x900-full.png) |
| 1280×800 | [Viewport](screenshots/locked-1280x800.png) · [Full page](screenshots/locked-1280x800-full.png) | [Viewport](screenshots/unlocked-1280x800.png) · [Full page](screenshots/unlocked-1280x800-full.png) |
| 768×1024 | [Viewport](screenshots/locked-768x1024.png) · [Full page](screenshots/locked-768x1024-full.png) | [Viewport](screenshots/unlocked-768x1024.png) · [Full page](screenshots/unlocked-768x1024-full.png) |
| 390×844 | [Viewport](screenshots/locked-390x844.png) · [Full page](screenshots/locked-390x844-full.png) | [Viewport](screenshots/unlocked-390x844.png) · [Full page](screenshots/unlocked-390x844-full.png) |

## Verification

Executed with isolated fixtures, temporary credential verifiers and mocked
upstream services:

```bash
.venv/bin/python -m pytest -q tests/test_public_dashboard.py \
  tests/test_recent_trade_snapshot.py tests/test_fleet_dashboard.py \
  tests/test_trade_lifecycle_dashboard.py
```

Result: **76 passed, 1 skipped**. The skipped JavaScript test was then run with
Playwright's bundled Node executable on PATH: **1 passed**. There are no remaining
skipped checks in that selected set. Two existing FastAPI/Starlette deprecation
warnings were reported.

The principal Chromium browser scenario passed after final styling. It verifies
all five viewport sizes, no page or compact-table overflow, seven-card ordering,
locked saved-ON values, password failure/success, per-asset sizing boundaries,
confirmation/cancellation, independent drafts and focus across polling, failed
saves, lost-response reconciliation without resubmission, trade detail precision,
LIVE/PAPER filtering, history paging, safe-stop refusals, cross-session isolation,
related-tab revocation, reload/background expiry, direct expired API rejection,
unconfigured cards and stale data retention. A second isolated Chromium scenario
also passed, covering empty versus unavailable history and confirmed per-asset
stopping with no unsupported restart button. Across the selected regressions and
two browser scenarios: **79 tests passed** (the initially skipped Node check was
run separately and passed).

Ruff passed for the modified/new Python files. Node syntax checking passed for
`viewer.js`. `git diff --check` passed for the public changes. Python compilation
of the public backend also passed. The full repository suite was not run.

## Practical limits

- No reference image was attached, and the reference website was inaccessible
  during inspection. Styling follows the supplied written specification.
- Five real trades, readable controls and full metrics require vertical scrolling
  even at 1920×1080; expanded disclosures and errors can add height. Nothing is
  scaled down or clipped to force the complete page into one screen.
- Browser validation used Chromium with synthetic fixtures, not production or
  other browser engines. No real orders, live-setting changes or bot stops were
  used for verification.
- Read-only full history is refreshed only when the cached source history changes;
  large histories can take longer to read. Existing mixed-history precedence is
  preserved upstream; the public adapter separates display modes without restoring
  paper trades replaced by verified live lifecycles.
- Restart is deliberately unavailable in the public UI because there is no
  supported backend start operation. Production rollout and public-service restart
  require explicit approval; instructions are in the public dashboard guide.
