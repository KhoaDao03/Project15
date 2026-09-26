# DOGE deployment — 2026-09-26

Deployed DOGE 15-minute markets at 20:00:31 UTC after existing managed positions reached market close and no unresolved orders remained. Both private and public dashboards now include ten assets, with DOGE reference prices displayed to seven decimal places and the existing one-second refresh retained.

DOGE uses the deployed HYPE preset with asset identity changed and ATR multiplier 1.10. The probability book cap remains +6 percentage points. Official KXDOGE15M metadata uses DOGEUSD_RTI; the exact strike comes from validated custom_strike.floor_strike because the top-level field truncates precision.

Validation: 547 distinct targeted tests passed across market parsing, configuration, fleet, execution logic, ATR, settlement, and dashboard/browser coverage. Scoped lint, JavaScript syntax and git diff checks passed. Production verification confirmed both rendered DOGE prices, fresh public data, ten market cards, all services active, and no browser JavaScript errors. See verification.json and public-doge.png/private-doge.png.

The deployment preserved all existing asset trading policies and initially left DOGE buying disabled. By subsequent verification, its policy had independently changed to enabled with 10 contracts (revision 2); verification left this setting intact. No orders were submitted by the deployment or verification scripts.

Backups and deployment scripts: data/deployments/doge-20260926/. Existing source changes were preserved.
