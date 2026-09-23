# Public dashboard

The DEKINGS visitor UI is served by `btc15 public-dashboard` on loopback port
8001. Its implementation is confined to `src/btc15/public_dashboard.py` and
`src/btc15/public_static/`. It uses FastAPI and plain HTML/CSS/JavaScript. There is
no frontend build and no change to the private dashboard or trading engine.

## Data and controls

| Public display or action | Existing source / behavior |
| --- | --- |
| BTC, ETH, SOL, XRP, GOLD, SILVER, OIL | Seven fixed cards. OIL targets backend asset `WTI`, whose verified reference is `Commodities.Index.PYTHOIL/USD`. Missing fleet members remain visible and disabled. |
| Reference price | `/api/fleet` already applies reference freshness checks. CF Benchmarks indices for crypto; Pyth indices for commodities. USD index values, not YES/NO contract prices. No daily-change feed is invented. |
| Bot status | Allowlisted operational state, connection and reason codes from `/api/fleet`, plus authoritative safe-stop status. Owner authorization and saved new-buy permission are separate. |
| LIVE / PAPER metrics | Public-only adapter over `/assets/{asset}/api/trades?mode=PAPER&include_open=true` for the configured run and default settlement-history scope. Verified `LIVE_FALLBACK` records are LIVE; remaining PAPER records are simulation. BACKTEST/unknown modes are excluded. |
| Win rate, streaks, completed trades, realized net P&L | Existing `lifetime_performance` helper applied in the public backend to the complete filtered history, never the latest five rows. Original combined/private accounting is unchanged. |
| Latest five / View all | Preserve source order: entry `opened` timestamp, falling back to record timestamp, descending with the source tie-breaker. Separate mode filtering occurs before taking five. All times are UTC. The public history drawer pages 50 sanitized records at a time. |
| New live buys / Contracts per entry / Apply | Existing `/api/control` with asset, current ticker, policy revision, integer quantity **1–20**, and explicit confirmation. Applies across future markets; changes future entries, not existing holdings. Disabling buys preserves exit management. |
| Stop Bot | Existing `/api/stop` with an explicit configured asset and confirmation, forwarded to that asset's safe-stop endpoint. Existing exposure/order safety refusals remain enforced. No liquidation or process-kill path. |
| Restart | Not supported by the backend. Start the service on the server through the normal operator process. No Start button is presented. |

LIVE history still follows the existing ownership/accounting exclusions. Verified
live fills replace overlapping paper lifecycles in the upstream source; PAPER is
the remaining simulation history, not an independent full backtest. Aggregate P&L
sums compatible per-asset values for the selected mode and explicitly marks partial
or stale totals. It is not account balance, portfolio value or unrealized profit.
Break-even trades count toward win rate and end either streak. Open trades are
excluded from realized metrics. A later settled outcome never determines an
earlier sale's P&L.

The public service polls the existing cached recent history every five seconds.
When that history snapshot changes, its read-only adapter loads complete history
in pages of up to 500, reusing the result until the next revision. The browser uses
one shared five-second refresh cycle, not seven market pollers. Full-history reads
may be slower for large ledgers. Failed/changed reads retain a marked stale view;
they never become a fabricated empty history. No ledgers or checkpoints are edited.

## Owner password and sessions

Use the existing interactive configuration utility on the server:

```bash
.venv/bin/python scripts/set_public_shutdown_passcode.py
```

The public service reads `PROJECT15_SHUTDOWN_SECRET_FILE`, currently configured by
the existing service unit. The file contains the established salted PBKDF2-SHA256
verifier, not a plaintext password. Enter the password locally in that utility or
the site's password field; never put it in source, a URL, documentation, or chat.
Without configuration, the access bar remains visible and explains that owner
access is unavailable.

The server enforces a fixed **60-second** session. Polling, navigation and activity
do not extend it. Reload initially locks the UI, then reads the server's remaining
time. Returning from a background tab revalidates authorization and cancels any
unsubmitted confirmation. Reauthentication requires a fresh password submission.

The visitor requests an HttpOnly, SameSite=Strict, `/api`-scoped cookie. It is Secure
on HTTPS. Loopback HTTP remains available for isolated local previews; production
must use the existing HTTPS reverse proxy, with the correct forwarded scheme.
The visitor receives no bearer token in its JSON response and stores no credential
in localStorage/sessionStorage. The established bearer API remains available to
existing clients. Each mutation independently checks the session and the existing
same-origin, body, revision, asset and quantity validations.

`GET /api/session` reports only enabled/authenticated state and remaining seconds.
`POST /api/lock` revokes the current session on the server and clears the cookie.
Same-browser tabs check server state; BroadcastChannel carries only a check hint,
never credentials. Other browser sessions remain independent. Failed
reauthentication revokes the current session. The existing five-attempts-per-minute
throttle remains in place. A public-service restart invalidates sessions.

Lock/unlock does not issue trading or lifecycle commands. Saved settings persist.
A failed or uncertain Apply retains the draft. An uncertain request is reconciled
against authoritative saved state without automatic resubmission. Owner-action
records use the existing Python logging infrastructure (`btc15.owner`) with action,
HTTP outcome, validated target and previous/requested settings; no passwords or
tokens. The public app writes timestamped audit records to stderr through its
scoped logger; the service manager can retain them in the journal.

## Validation and preview

Public API regression tests (mock upstream services and isolated credentials):

```bash
.venv/bin/python -m pytest -q tests/test_public_dashboard.py
```

The optional browser test starts a temporary loopback app with a mock upstream;
it cannot place exchange orders or stop an actual service. Install Playwright and
its Chromium browser in a development environment, then run:

```bash
PUBLIC_SCREENSHOT_DIR=reports/public-dashboard/screenshots \
  .venv/bin/python -m pytest -q tests/test_public_browser.py
```

Screenshots visibly identify synthetic fixtures. They include locked and unlocked
views at 1920×1080, 1440×900, 1280×800, 768×1024 and 390×844. Files with `-full` show
the entire scrollable page; other files have the exact requested viewport size.
At 1600px and wider, the layout uses four equal cards then three equal cards;
below that it uses two columns, then one below 700px. Long data and expanded
information can increase page height rather than shrink text or clip content.

## Deployment — requires explicit approval

No deployment or service restart is performed by these tests. Before deploying,
review the public-only diff and approve the target host and service restart. Ship
`public_dashboard.py` and the three files in `public_static/` together, preserving
all existing runtime data and configuration. Restart **only** the existing
`project15-public-dashboard` service using the installation's user/system service
scope. Do not restart collectors, execution, or the private dashboard for this UI
change. The public backend consumes their existing routes unchanged.

Verify the read-only public page, HTTPS/cookie attributes, configured asset states,
and data timestamps. Owner mutations must not be used as a production smoke test.
Rollback consists of restoring those four public files and restarting only the
public service; no data migration is involved.
