# Live trading: NOT READY

This release never submits production orders. Both activation flags and the live
adapter fail closed. `live_payload` implements the documented V2 bid/ask direction,
YES-leg price, fixed-point count, shard, pause cancellation and reduce-only intent
for review/testing. `KalshiClient.reconciliation` retrieves paginated orders,
fills and positions read-only. Neither is an execution/reconciliation state machine.

Required before production activation: explicit user approval after reviewing
calibration and paper evidence; persistent account/position/order reconciliation;
unknown-submission recovery by client order ID; actual fee/account alignment;
exchange-specific live risk and collateral; pause/lifecycle/cancel-race testing;
clock and stale-data rechecks through network submission; rate-limit accounting;
kill-switch/cancellation recovery; and deployment/secrets/access review.

Changing .env flags is insufficient. A separately reviewed code change is required
to implement live submission. This choice is stricter than two environment flags
because the requested live prerequisites have not been empirically validated.
The cycle remains collect → analyze → hypothesis → replay → held-out validation →
paper → review → explicit user approval → live change. No analyst endpoint can
promote a model or alter live parameters automatically.
