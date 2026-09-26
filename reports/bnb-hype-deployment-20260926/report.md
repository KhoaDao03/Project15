# BNB and HYPE deployment

Deployed September 26, 2026 at 01:17 UTC on the existing production host.

- Added isolated BNB and HYPE signal collectors to the fleet manifest, enabled their systemd services, and copied ETH-equivalent frozen configurations. ATR multipliers are BNB 1.05 and HYPE 1.10; live evaluations confirmed both values.
- Reloaded the executor, private dashboard and trusted public publisher. Updated the isolated public website's server and static files without changing its systemd isolation or dependencies.
- The nine assets appear in both dashboards. Browser checks confirmed BNB/HYPE selection, working private asset pages and no JavaScript errors. Public HTTPS snapshot and history endpoints returned fresh data; the served JavaScript exactly matched the reviewed source.
- All nine collector services, executor, private dashboard, publisher and public website were active at verification. New reference feeds were connected, with reference ages below one second. Warmup and other entry gates remain enforced.
- BNB and HYPE have explicitly saved **disabled** live-buy policies with quantities of 10. Deployment did not enable real-money buying or place trades. Existing seven asset policies, quantities, configurations and histories were preserved.

The additive reload took place outside the entry window after confirming zero unresolved orders and zero active managed positions in the execution journal. Existing collectors continued running. No checkpoint hashes were modified and no historical journals were restored.

Private backups are at `data/deployments/bnb-hype-20260926/`, including an online SQLite execution-journal backup, the previous manifest, frozen configurations, user services, source and prior public files. The deployment script and completion record are retained there.

[Verification](verification.json) and [browser results](browser.json) record the deployment checks. The previous implementation turn completed 527 targeted regression checks; no heavy replay/test workload was rerun during this production cutover.
