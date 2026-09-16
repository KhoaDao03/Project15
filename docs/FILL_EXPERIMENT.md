# Paper fill experiment

The opt-in `config/settlement-edge-fill-paper.json` retains the original entry
thresholds and passive pricing, with two changes:

* Pending orders recheck price bounds, fees and net edge at their existing limit.
  Current spread, liquidity, favored side, probability, quality, entry window,
  market status and feed freshness must still pass. It does not chase the ask or
  extend the 20-second timeout.
* A completely unfilled cancelled order may retry once, at least five seconds
  after cancellation, if current entry and execution checks pass. A partial fill
  blocks retry even if the position later closes. Each submission consumes the
  existing daily attempt and exposure budgets. Attempt count and cancellation
  time survive checkpoint recovery; each attempt has its own durable claim.

Cancellation records now include individual failed checks, checked price, limit,
net EV, elapsed time and remaining simulated queue. These appear in the order
history details. Historical generic cancellations cannot be retrospectively
assigned a specific cause.

The separate `config/settlement-edge-fill-taker-paper.json` uses the same settings
with `passive=false`: after simulated latency it takes the current fresh displayed
asks, best price first, up to the original price cap. The cap is the submission
ask plus the slippage allowance, rounded down to a valid tick and capped by the
maximum entry price. Slippage is a price-movement allowance, not a surcharge
added to every fill. Each fill pays taker fees at its actual execution price;
recorded slippage is measured from the submission ask. It cancels any remainder.
A heartbeat or reference event can trigger matching without waiting for another
quote update; all reference/book/market checks must still pass. IOC limit
revalidation does not deduct the already-included slippage allowance twice.
The original passive preset remains available for comparisons.
Neither preset changes the model's probability calibration or guarantees fills.

## Run one deliberately named experiment

Safely stop the previous writer and resolve its open inventory first. Back up the
database and freeze a copy of the selected preset under `data/runtime/`. Do not
edit an existing run's frozen configuration or clear its risk history. For example,
after these checks, copy the passive preset once (exclusive creation):

```bash
uv run --locked python -c "from pathlib import Path; p=Path('data/runtime/settlement-fill-v1.json'); p.parent.mkdir(parents=True, exist_ok=True); p.open('x').write(Path('config/settlement-edge-fill-paper.json').read_text())"
uv run --locked btc15 --config data/runtime/settlement-fill-v1.json dashboard --run-id settlement-fill-v1 --port 8000
```

Repeat exactly that command to resume. The original and moderate presets retain
their thresholds and configuration hashes. The new settings are opt-in;
loading newer code does not silently change a frozen control experiment.

Spread and net-edge boundary comparisons use decimal price arithmetic so an
exact 4-cent spread is accepted by a 4-cent cap, while a larger spread is rejected.

## Evaluate the experiment

Use Orders/cancellations to distinguish submissions from fills. Compare original,
passive fill, and IOC runs separately: submitted orders, orders with any fill,
filled/submitted quantity, cancellations by reason, net results after fees and
post-fill reference/quote movement in retained tapes. Use unique order IDs when
counting attempts, including retries. Synthetic regressions establish mechanics,
not an empirical improvement in fill rate or profitability.
