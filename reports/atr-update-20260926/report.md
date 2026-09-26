# ATR multiplier update — 2026-09-26

Updated and deployed BTC 0.80, ETH 0.80, SOL 1.00, XRP 1.25, BNB 1.15 and HYPE 0.85. DOGE remains 1.10; commodity multipliers are unchanged.

Updated the model constants, numeric expectations and active model documentation. All 217 targeted regression tests passed; Ruff and diff checks passed. The initial sandboxed test run stalled at local TestClient usage and was interrupted; the complete elevated run passed.

Restarted only the six affected signal collectors during the early market cycle after verifying no active managed positions or unresolved orders. Each collector subsequently reported the requested multiplier and a fresh connected reference feed. All existing live trading policies were preserved. No orders were submitted by the deployment script.

Backups and deployment script: data/deployments/atr-20260926/. Production evaluation evidence: verification.json.
