# Strategy and assumptions

The question is whether the probability of the **settlement average** satisfying
the contract is higher than the executable cost after fees and slippage. A favored
side follows the actual comparator and reference/strike relationship; it is not a
standalone signal. Exactly at the strike, V1 does not choose a favored side.

Default entry window: `120 < close_time−now <= 480`. Suggested .85 minimum price,
.99 maximum price, .90 minimum conservative probability, quality ≥85 and net
edge/EV ≥.03 are configurable **ASSUMPTIONS**. Prices must have adequate executable
depth, spread ≤.04, valid metadata and fresh feeds. EXTREME regimes are rejected.
No indicators or Kalshi-implied prices dominate the base probability model.

For one purchased binary contract: `EV=p*(1-c)-(1-p)*c-fees-slippage`, equivalently
`p-c-fees-slippage`. Ask already incorporates crossing the spread, so the spread
is not subtracted a second time. Entry uses conservative side probability and an
upper fee estimate. Partial-fill fees are charged at execution. Remaining orders
are cancelled when the signal deteriorates, the side changes, data ages, the
no-entry boundary arrives, or the configured patience expires.

Passive quotes discount the ask by at most the configured discount, half-spread
and available edge, then snap to a valid tick. They never reprice upward after an
invalid signal. V1 makes **one entry attempt per market per run**; cancel/replace
and automatic chasing are deliberately not enabled until execution validation.
Aggressive entry is an explicit config choice with latency and depth-limited IOC.

Sizing modes: fixed contracts, fixed dollars or bankroll percentage, always capped
by worst-case cost, bankroll percentage, trade contracts/dollars, open exposure,
daily gross exposure, daily net loss and daily order count. Cancelled attempts
still consume the daily attempt/exposure budget. No martingale or loss-based sizing.

Exits: executable configured TP, probability invalidation/hold versus net sell
value, or entry-price × .75 hard stop. Otherwise wait for official settlement.
Stops are not guaranteed; full worthless settlement is the sizing baseline. TP
rounds upward to the actual market grid and still must cover simulated slippage.

Every numeric default in `config/defaults.json` is an explicit research assumption
except constants validated as contract semantics. Do not treat these settings as
optimal. No experiment updates running paper or live configuration. The required
improvement sequence remains collect → hypothesis → replay → held-out validation
→ paper validation → review → explicit approval before a live change.
