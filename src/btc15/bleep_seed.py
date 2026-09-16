"""Recorded exchange candles for Bleep indicators only; never settlement samples."""

import math
from datetime import datetime, timezone

import httpx

from .strategies.settlement_edge.bleep import indicator_inputs


def validate_seed(body, now, asset="BTC"):
    if body.get("asset", "BTC") != asset:
        raise ValueError("Bleep seed asset does not match this run")
    if body.get("version") != 1 or body.get("provider") not in ("coinbase", "kraken", "binance"):
        raise ValueError("Invalid Bleep seed provider/version")
    candles = body.get("candles", [])
    if not 33 <= len(candles) <= 120:
        raise ValueError("Invalid Bleep seed length")
    previous = None
    for c in candles:
        if len(c) != 5 or not all(type(v) in (int, float) and math.isfinite(v) for v in c):
            raise ValueError("Invalid Bleep candle")
        minute, op, high, low, close = c
        if minute != int(minute) or low <= 0 or not low <= min(op, close) <= max(op, close) <= high:
            raise ValueError("Invalid Bleep OHLC")
        if (minute + 1) * 60 > now or (previous is not None and minute != previous + 1):
            raise ValueError("Noncausal or noncontiguous Bleep seed")
        previous = minute
    if now - (candles[-1][0] + 1) * 60 > 180:
        raise ValueError("Stale Bleep seed")
    return {c[0]: tuple(c) for c in candles}


async def fetch_seed(client, now, asset="BTC"):
    from .assets import asset_spec

    asset_spec(asset)

    def iso(t):
        return datetime.fromtimestamp(t, timezone.utc).isoformat()

    sources = [
        (
            "coinbase",
            f"https://api.exchange.coinbase.com/products/{asset}-USD/candles",
            dict(granularity=60, start=iso(now - 101 * 60), end=iso(now)),
        ),
        (
            "kraken",
            "https://api.kraken.com/0/public/OHLC",
            dict(pair="XBTUSD" if asset == "BTC" else asset + "USD", interval=1),
        ),
        (
            "binance",
            "https://api.binance.com/api/v3/klines",
            dict(symbol=asset + "USDT", interval="1m", limit=101),
        ),
    ]
    errors = []
    for provider, url, params in sources:
        try:
            response = await client.get(url, params=params, timeout=8)
            response.raise_for_status()
            rows = response.json()
            if provider == "coinbase":
                candles = [(r[0] / 60, r[3], r[2], r[1], r[4]) for r in rows]
            elif provider == "kraken":
                rows = next(v for k, v in rows["result"].items() if k != "last")
                candles = [(float(r[0]) / 60, *map(float, r[1:5])) for r in rows]
            else:
                candles = [(r[0] / 60000, *map(float, r[1:5])) for r in rows]
            candles = sorted(c for c in candles if (c[0] + 1) * 60 <= now)[-100:]
            body = dict(version=1, provider=provider, candles=candles)
            if asset != "BTC":
                body["asset"] = asset
            validate_seed(body, now, asset)
            return body
        except (ValueError, KeyError, TypeError, IndexError, StopIteration, httpx.HTTPError) as exc:
            # Public provider failure must not stop the official reference feed.
            errors.append(f"{provider}: {type(exc).__name__}")
    raise ValueError("; ".join(errors))


def seeded_inputs(candles, seed_last_minute, ticks, now):
    """Roll up observed BRTI minutes after the seed; cap the rolling window at 120."""
    grouped = {}
    for tick in ticks:
        minute = int(tick.source // 60)
        if tick.source <= now and tick.received <= now and minute > seed_last_minute:
            grouped.setdefault(minute, []).append(tick.price)
    for minute, prices in grouped.items():
        previous = candles.get(minute)
        candles[minute] = (
            minute,
            previous[1] if previous else prices[0],
            max(max(prices), previous[2] if previous else max(prices)),
            min(min(prices), previous[3] if previous else min(prices)),
            prices[-1],
        )
    for minute in list(candles):
        if minute < int(now // 60) - 119:
            del candles[minute]
    ordered = [candles[k] for k in sorted(candles)]
    return indicator_inputs(ordered)
