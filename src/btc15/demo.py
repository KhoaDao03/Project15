"""Synthetic integration fixture; never mislabeled as historical BRTI data."""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .assets import asset_spec
from .domain import dumps


def generate(path, start=1788901200.0, asset="BTC"):
    selected = asset_spec(asset)
    scale = {"BTC": 1, "ETH": 0.04, "SOL": 0.002, "XRP": 0.00002}[asset]
    start = float(start)  # Fixed reproducible timestamp, unrelated to a recorded market.
    close = start + 900

    def prose(ts):
        return (
            datetime.fromtimestamp(ts, ZoneInfo("America/New_York"))
            .strftime("%I:%M %p %Z on %b %d, %Y")
            .lstrip("0")
        )

    def iso(ts):
        return datetime.fromtimestamp(ts, timezone.utc).isoformat()

    ticker = f"{selected.series}-SYNTHETIC{int(start)}-00"
    series = dict(
        ticker=selected.series,
        frequency="fifteen_min",
        exchange_index=2,
        fee_type="quadratic",
        fee_multiplier=1,
        settlement_sources=[dict(name="CF Benchmarks")],
    )
    market = dict(
        ticker=ticker,
        event_ticker=selected.series + "-SYNTHETIC",
        market_type="binary",
        title=f"SYNTHETIC {asset}15 fixture",
        open_time=iso(start),
        close_time=iso(close),
        expiration_time=iso(close + 604800),
        status="active",
        floor_strike=78000 * scale,
        exchange_index=2,
        notional_value_dollars="1.0000",
        strike_type="greater_or_equal",
        custom_strike={"round_digits": str(selected.round_digits)},
        rules_primary=f"If the simple average of the sixty seconds of CF Benchmarks' {selected.rule_index} before {prose(close)} is at least the simple average of the sixty seconds of CF Benchmarks' {selected.rule_index} before {prose(start)}, then the market resolves to Yes.",
        rules_secondary=f"60 RTI prices are collected. The average is rounded to the nearest {selected.round_digits} decimal places.",
        price_ranges=[
            dict(start="0.00", end="0.10", step="0.001"),
            dict(start="0.10", end="0.90", step="0.01"),
            dict(start="0.90", end="1.00", step="0.001"),
        ],
    )
    rows = []

    def add(payload, now):
        rows.append(
            dict(
                id=f"synthetic-{len(rows):06d}",
                received=now,
                monotonic_ns=int((now - start + 1800) * 1e9),
                connection_id="SYNTHETIC",
                payload=dumps(payload),
            )
        )

    add(
        dict(
            type="metadata",
            msg=dict(
                series=series,
                markets=[market],
                clock_skew=0,
                exchange_status={"trading_active": True},
                fee_changes={market["event_ticker"]: []},
                series_fee_changes=[],
                synthetic=True,
            ),
        ),
        start - 1800,
    )
    # Thirty minutes of causal warmup plus the complete contract, with independent channels.
    for i in range(-1799, 902):
        now = start + i
        price = (78200 + 8 * math.sin(i * 0.11) + 3 * math.sin(i * 0.43)) * scale
        add(
            dict(
                type="cfbenchmarks_value",
                sid=1,
                seq=i + 1800,
                msg=dict(
                    index_id=selected.index,
                    data=json.dumps(dict(type="value", id=selected.index, time=now * 1000, value=str(price))),
                ),
            ),
            now,
        )
        if i >= 0:
            # A cheaper late quote also exercises Bleep with cost-filtered presets.
            # Unified YES-leg prices: NO-side .90 means NO bid .10, YES ask .90.
            add(
                dict(
                    type="orderbook_snapshot",
                    sid=2,
                    seq=i + 1,
                    msg=dict(
                        market_ticker=ticker,
                        yes_dollars_fp=[["0.88" if i < 600 else "0.86", "2.00"]],
                        no_dollars_fp=[["0.90" if i < 600 else "0.88", "100.00"]],
                    ),
                ),
                now + 0.01,
            )
            if 425 <= i < 780:
                add(
                    dict(
                        type="trade",
                        msg=dict(
                            market_ticker=ticker,
                            trade_id=f"demo-{i}",
                            taker_outcome_side="no",
                            yes_price_dollars=".88" if i < 600 else ".86",
                            count_fp="2.00",
                            ts_ms=(now + 0.02) * 1000,
                        ),
                    ),
                    now + 0.02,
                )
    add(dict(type="settlement", msg=dict(market_ticker=ticker, result="yes")), close + 3)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(dumps(r) + "\n" for r in rows))
    return target
