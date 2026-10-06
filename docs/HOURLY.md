# ETH, XRP and HYPE hourly ladders

`ETHD` (`KXETHD`), `XRPD` (`KXXRPD`) and `HYPED` (`KXHYPED`) are separate fleet assets, stores,
controls and dashboard histories. Only above/below ladders are supported. The
existing ETH/XRP/HYPE 15-minute presets and their configuration versions are unchanged.

| Setting | ETHD | XRPD | HYPED |
| --- | --- | --- | --- |
| ATR sigma multiplier | 1.00 | 1.10 | 1.40 |
| Confidence cap premium over selected-side midpoint | 6 percentage points | 10 percentage points | 6 percentage points |
| Probability floor, after cap | 83% | 83% | 83% |
| Entry window | `60 < seconds_left <= 600` | Same | Same |
| Ask band | 80–96¢ | Same | Same |
| Contracts per strike | 10 | 10 | 10 |
| Bought or pending strikes per event | At most 2 | At most 2 | At most 2 |
| Stops / take-profit / blackout | Disabled | Disabled | Disabled |
| `entry_limit_offset` | `null` | `null` | `null` |

Only markets closing at the **next top of the hour** are discovered, regardless
of how many hours or days earlier they opened. Daily/weekly openings that close
at that hour are included. Otherwise later events are excluded until rollover.
A strike can be bought once; killed zero-fill attempts release their event slot.
Partial fills, unresolved buys and earlier filled buys consume slots, including
after restart. Among the tracked strikes, first qualifying signals have priority;
simultaneous candidates are ordered by descending `volume_fp`. Unfilled retries
still require a fresh signal and known prior outcome.

Only **two strikes per hourly bot** receive continuous live-book processing. On
each metadata refresh (normally every 15 seconds plus request time), the collector
ranks the next-hour ladder using REST quotes and the current ATR probability
model. Ranking minimizes probability shortfall below 83% plus ask-price distance
outside 80–96 cents. Passing both checks ranks first; ties retain current selections,
then favor higher volume. During reference/model warmup, quote-only ranking
bootstraps subscriptions; it never authorizes a trade. Filled or pending strikes
take priority and consume these two slots. Existing obligations are preserved
even if manual activity has created more than two.

Unselected books are unsubscribed, excluded from evaluation/recovery/display, and
their old candidate signals are cleared. Metadata identities and trade evidence
remain available for settlement/audit. Newly selected strikes require fresh
sequenced snapshots and every original entry check. Other 15-minute bots keep
their existing subscription behavior.

The existing ATR-finish formula, warmup, reference feeds and indicator lean remain
in use. Sustained-lead confirmation stays enabled with the inherited normal
settings; `late_entry_enabled=false` makes the cutoff 60 seconds and prevents the
late branch and its separate floor from running. Spot equal to strike favors YES
for the signal; settlement **strictly above** the strike is YES, equality is NO.

Hourly rules specify an unrounded arithmetic mean of the final 60 one-second CF
samples. We compare that mean directly to the decimal strike; we do not apply the
15-minute contracts' rounding. The settlement-distribution boundary uses that
same unrounded strike. The existing 15-minute parser body, comparison behavior
and model results remain unchanged. Captured API fixtures are in
`tests/fixtures/{ethd,xrpd}-hourly-20261006.json`. The captured XRP
`settlement_timer_seconds` is 1800 (ETH: 60): that metadata delay does not change
the 60-second averaging window stated in the contract rules. Unknown rule wording
fails closed.

The owner's supplied 59-day replay motivates the event cap and holding to
settlement. Its profit/slippage estimates have not been independently reproduced
by this implementation. It omitted the indicator lean retained here.

## HYPED addition

HYPED uses the same two-strike selection, freshness, event cap and settlement
machinery as ETHD/XRPD. Its preset copies ETHD with only `asset` changed.
The frozen HYPED version is `9e1dff29e24034e1`.
ETHD remains `3efb5d90d16f9d9b`, XRPD remains `e546a5ad1fc8175d`, and the 15-minute
HYPE sigma multiplier remains 1.15.

The owner disabled HYPED's initially requested 1¢ entry-limit offset on
October 6, 2026. All three hourly presets now use `entry_limit_offset: null`,
so the buy limit can reach the configured 96¢ maximum even when the signal ask
is lower. The optional tighter limit remains available and tested.

The public API fixtures captured on October 6 include 1 PM and 3 PM ordinary
hourly events and the 5 PM daily event. All say `HYPEUSD_RTI` and `above $92.9999`;
secondary wording matches ETHD exactly. The parser permits the optional `$` only
for HYPED and still requires a matching decimal strike, index, rule time and
secondary text. The 1 PM and 3 PM ladders had 300 strikes each; 5 PM had 40.
All remain subject to next-top-of-hour discovery and two live-book selections.
See `tests/fixtures/hyped-hourly-20261006.json`; the settled 1 PM capture is
explicitly replayed as active by tests that simulate pre-close execution.

Deploy HYPED as a live-signal collector with **new live buys disabled**. There is
no paper-trading trial. Khoa separately enables `HYPED` in the private dashboard
with `ENABLE_REAL_TRADING` at 10 contracts. Existing asset permissions are not
changed. The executor's global loss guard includes every manifest member,
including HYPED and its separate trade history.

## Go-live check

On October 6, 2026, the owner requested reverting the hourly names to ETHD/XRPD after startup.
Both collectors are now started and enabled at boot; the private/public dashboards
and executor membership were reloaded. **Hourly live buying remains disabled.**
On the current workspace,
`data/cloud/ETHD.json`, `XRPD.json` and the two manifest rows are already prepared.
Their versions are `3efb5d90d16f9d9b` and `e546a5ad1fc8175d`, respectively.
Both ship with `entry_limit_offset: null` and no enabled live policy.
The rename back to ETHD/XRPD passed 185 targeted tests. The temporary ETH1H/XRP1H
collectors are disabled; their recorded data is retained for audit.


The first live startup exposed repeated reconnects caused by inactive far-out
strikes aging while other ladder books continued updating. Hourly collector
recovery now tolerates those idle books only while another sequenced, valid book
is fresh. Missing snapshots, integrity failures, stale references and a fully
stalled ladder still block/recover. Per-strike signal and execution freshness
checks are unchanged, so an idle strike cannot qualify for entry. Existing
15-minute recovery behavior is unchanged. Recovery and hourly entry tests passed
(97 tests), in addition to 138 rename, contract, dashboard and signal tests.
Discovery now requests only the next hourly close using the API's close-time
bounds, avoiding a scan of later events before each connection/metadata refresh.
All strikes at that close remain included. The hourly contract and discovery
suite passed (42 tests); public browser/research-control checks passed (17 tests).
The [API filter compatibility rules](https://docs.kalshi.com/api-reference/market/get-markets)
require omitting the open/unopened status filter with close-time bounds; exact
close matching and contract validation still happen locally.

1. Deploy the reviewed local commits on `cloud-deploy` to the trading checkout.
   Retain existing credentials, state, service overrides and frozen configs.
   For a **fresh** fleet, `scripts/prepare_cloud.py` now includes all three hourly
   presets. Never run fresh preparation over the existing data directory.
   To add only hourly members to another existing fleet, from the repo root run:

   ```bash
   .venv/bin/python - <<'PY'
   import json
   from pathlib import Path
   from btc15.config import Strategy
   from btc15.fleet import load_members
   root = Path('data/cloud')
   path = root / 'manifest.json'
   rows = json.loads(path.read_text())
   for row in json.loads(Path('deploy/cloud/hourly-manifest.json').read_text()):
       old = next((r for r in rows if r['asset'] == row['asset']), None)
       assert old is None or old == row, 'Existing hourly deployment differs'
       source = Path('config/settlement-edge-' + row['asset'].lower() + '-paper.json')
       target = root / row['config']
       if target.exists():
           assert Strategy.load(target).version == Strategy.load(source).version
       else:
           with target.open('x') as f:
               f.write(source.read_text())
       if old is None:
           rows.append(row)
   prepared = root / 'manifest-hourly-ready.json'
   prepared.write_text(json.dumps(rows, indent=2) + '\n')
   assert len(load_members(prepared)) == len(rows)
   prepared.replace(path)
   PY
   ```

2. Keep `TRADING_MODE=PAPER` and `ENABLE_LIVE_TRADING=false`. Real orders use the
   separate existing executor and owner dashboard authorization. Start collectors
   and reload the fleet readers when ready:

   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now project15-signal@ETHD project15-signal@XRPD project15-signal@HYPED
   systemctl --user restart project15-execution project15-dashboard
   systemctl --user status project15-signal@ETHD project15-signal@XRPD project15-signal@HYPED --no-pager
   journalctl --user -u project15-signal@ETHD -u project15-signal@XRPD -n 60 --no-pager
   ```

   In the private dashboard, open `/assets/ETHD`, `/assets/XRPD` and `/assets/HYPED`. Confirm the
   collector is healthy, reference/book ages are fresh, parsed strikes appear,
   and every **tradable** market closes at the next top of the hour. Historical
   held/settling contracts may remain visible for settlement recovery. Verify
   the hourly asset labels and separate histories on the public dashboard.
   Initial full-ladder synthetic tests used 300 ETH and 75 XRP books. Live book
   subscriptions are now narrowed to the two selected strikes described above. See the [implementation report](../reports/hourly-20261006/report.md)
   for measured costs. Check production processing lag stays below 2 seconds.

3. Deploy the isolated public site's asset-list changes using the existing
   reviewed staging workflow (the commands below assume its venv already exists):

   ```bash
   .venv/bin/python scripts/stage_public_site.py /srv/public-web/project15
   chown -hR root:public-web /srv/public-web/project15
   chmod -R g+rX,o-rwx /srv/public-web/project15
   systemctl --user restart project15-public-export
   systemctl restart project15-public-site
   ```

   See [public isolation](PUBLIC_WEBSITE_ISOLATION.md) for first installation.
   No trading credentials or journals belong in the public installation.

4. Khoa enables **ETHD**, **XRPD** and **HYPED** separately in the private dashboard's live
   buying control, confirming `ENABLE_REAL_TRADING`, with **10 contracts**.
   Verify stop price **0** and no take-profit. Buying stays disabled until this
   owner action; collectors themselves cannot place real orders.

The existing global loss guard automatically includes every manifest member,
including hourly histories. **Its actual implementation is a latched −$50
combined dashboard realized-P&L cutoff, not a −$50/day reset.** This change does
not alter that policy or reset an existing latch. A latched guard must be handled
through the existing owner controls before any new buying can occur.

## Fill quality and review

The private `data/cloud/manual-orders.sqlite` journal stores each bot hourly buy's
`timing.hourly_entry`: `signal_ask`, `limit_sent`, `fill_price`,
`fill_minus_signal_ask`, `seconds_left`, `strike`, `event_ticker`,
`model_probability`, `capped_probability`, and `signal_timestamp`. The observation
also travels with immutable execution-journal order snapshots. Fill price is
exchange-reported maker plus taker fill cost divided by filled contracts,
excluding fees. Unfilled orders and fills with missing exchange cost data have no fill price. Read without changing state:

```bash
.venv/bin/python - <<'PY'
import json, sqlite3
with sqlite3.connect('file:data/cloud/manual-orders.sqlite?mode=ro', uri=True) as db:
    for (body,) in db.execute("SELECT body FROM manual_orders WHERE json_extract(body, '$.timing.hourly_entry') IS NOT NULL ORDER BY rowid DESC LIMIT 100"):
        row = json.loads(body)
        print(row['request']['ticker'], row['state'], row['timing']['hourly_entry'])
PY
```

After approximately **100 filled ETHD trades**, compare the mean fill price minus
signal ask against **1¢**, and check mean realized net P&L per 10-contract trade
is **positive**, including exchange fees. Use completed hourly dashboard trades
for net P&L, not gross fill differences. If slippage exceeds 1¢, consider setting
`entry_limit_offset` to `0.01`: this caps the order at `min(signal ask + 1¢, 96¢)`.
It ships **off** (`null`, limit 96¢); killed orders follow normal fresh-signal retry
rules. The option is validated from 0 to 5¢ and changes the config hash only when
set. Activate changed configs through the existing freeze/restart/policy-version
workflow; editing only the source preset does not change a running collector.

Pause the asset's new live buys if mean realized net is **below −$0.10 per trade
after 150 completed trades**. Holding/settlement management continues when buying
is disabled. These are the owner's requested review points, not guaranteed
performance thresholds.
