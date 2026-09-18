"""Clearly synthetic source stream, including synthetic options and binary quotes."""

import math
from dataclasses import asdict
from decimal import Decimal

import numpy as np

from .engine import Engine
from .recording import Recording
from .schema import YEAR, ContractSpec, Reference, digest

START = 1789646400  # Fixed UTC clock makes the example reproducible.


def synthetic_events(*, markets=2, step=15, seed=701):
    rng = np.random.default_rng(seed)
    price = 76304.78
    for market in range(markets):
        start = START + market * 900
        end = start + 900
        target = f"{price:.2f}"
        ticker = f"SYNTHETIC-BTC-{market + 1}"
        spec = ContractSpec(
            "SYNTHETIC-BTC15",
            ticker,
            f"SYNTHETIC-EVENT-{market + 1}",
            target,
            "synthetic generator; not exchange metadata",
            start,
            end,
            end,
            tuple(range(end - 59, end + 1)),
            "BRTI",
            ">=",
            True,
            "ROUND_HALF_UP",
            2,
            "1",
            "USD",
            "active",
            "synthetic://rules",
            digest({"rule": "synthetic sixty samples; half-up cents; >=", "target": target}),
            start,
            verified=True,
            synthetic=True,
        )
        yield "contract", {"normalized": asdict(spec), "raw": None}, start, None
        known = []
        for second in range(901):
            now = start + second
            if second:
                price *= math.exp(-0.5 * 0.30**2 / YEAR + 0.30 / math.sqrt(YEAR) * rng.standard_normal())
            value = f"{price:.8f}"
            ref = Reference(value, now, now, source="synthetic-brti")
            yield "reference", {"normalized": asdict(ref), "raw": {"synthetic": True}}, now, now
            if now > end - 60:
                known.append(Decimal(value))
            if second % 30 == 0:
                quotes = []
                for k in (float(target) * 0.97, float(target), float(target) * 1.03):
                    quotes.append(
                        dict(
                            instrument=f"SYNTHETIC-OPTION-{k:.0f}",
                            strike=k,
                            option_type="call",
                            expiry=start + 30 * 3600,
                            quote_currency="BTC",
                            settlement_currency="BTC",
                            instrument_type="reversed",
                            settlement_period="day",
                            forward=price * 1.0002,
                            index_price=price,
                            underlying_index="synthetic-forward",
                            source_time=now,
                            received_time=now,
                            bid=0.009,
                            ask=0.010,
                            bid_size=2.0,
                            ask_size=3.0,
                            mark=0.0095,
                            open_interest=10.0,
                            volume=2.0,
                            mark_iv=0.30 + 0.10 * abs(math.log(k / price)),
                            bid_iv=0.25,
                            ask_iv=0.40,
                            iv_unit="annualized_decimal",
                            original_iv_unit="percent",
                            state="open",
                        )
                    )
                yield "options", {"normalized": quotes, "raw": {"synthetic": True}}, now, now
            if second % step == 0 or second in (300, 600, 780, 840, 870, 890, 899, 900):
                # Independent illustrative quote generation, not a tuned lead/lag model.
                mid = min(0.98, max(0.02, 0.5 + (price - float(target)) / 500 + rng.normal(0, 0.02)))
                yes = f"{max(0, mid - 0.02):.4f}"
                no = f"{max(0, 1 - mid - 0.02):.4f}"
                yield (
                    "book",
                    {
                        "market_ticker": ticker,
                        "raw": {"synthetic": True},
                        "normalized": {
                            "type": "rest_snapshot",
                            "msg": {
                                "orderbook_fp": {
                                    "yes_dollars": [[yes, "10.25"]],
                                    "no_dollars": [[no, "12.50"]],
                                }
                            },
                        },
                    },
                    now,
                    now,
                )
                yield "forecast_tick", {}, now, None
        yes = spec.yes(sum(known) / 60)
        yield "outcome", dict(market_ticker=ticker, yes=yes, source="synthetic-exact-average"), end, None


def create_recording(path, config, *, markets=2, step=15):
    recording = Recording(path, "synthetic", config)
    engine = Engine(config, "synthetic")
    if recording.seq:
        recording.close()
        raise ValueError("Synthetic output must be new; use replay for existing recordings")
    try:
        for kind, data, received, source in synthetic_events(markets=markets, step=step):
            event = recording.append(kind, data, received, received, source)
            recording.forecasts(event["seq"], engine.apply(event))
    finally:
        recording.close()
