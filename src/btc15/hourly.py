"""Narrow parser for CF Benchmarks ETH/XRP/HYPE hourly above/below ladders."""

import hashlib
import re
from datetime import datetime
from decimal import InvalidOperation
from zoneinfo import ZoneInfo

from .domain import D, Market, SettlementSpecification, timestamp


def parse_hourly_market(raw, series, asset):
    if series.get("frequency") != "hourly":
        raise ValueError("Not a supported hourly series")
    event = raw.get("event_ticker", "")
    if not re.fullmatch(re.escape(asset.series) + r"-\d{2}[A-Z]{3}\d{4}", event):
        raise ValueError("Invalid hourly event identity")
    try:
        strike = D(raw.get("floor_strike"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Invalid hourly strike") from exc
    if not strike.is_finite() or strike <= 0:
        raise ValueError("Invalid hourly strike")
    match = re.fullmatch(re.escape(event) + r"-T([0-9]+(?:\.[0-9]+)?)", raw.get("ticker", ""))
    if not match or D(match[1]) != strike:
        raise ValueError("Hourly ticker/strike conflict")
    if (
        raw.get("market_type") != "binary"
        or raw.get("strike_type") != "greater"
        or raw.get("cap_strike") is not None
        or raw.get("custom_strike") is not None
    ):
        raise ValueError("Only hourly above/below strikes are supported")
    primary, secondary = raw.get("rules_primary", ""), raw.get("rules_secondary", "")
    index = {"ETHD": "Ethereum Real-Time Index (ERTI)", "XRPD": "Ripple-Dollar Real Time Index (XRPUSD_RTI)", "HYPED": "HYPEUSD_RTI"}[
        asset.symbol
    ]
    dollar = r"\$?" if asset.symbol == "HYPED" else ""
    match = re.fullmatch(
        rf"If the simple average of the sixty seconds of CF Benchmarks' {re.escape(index)} "
        rf"before (.+?) is above {dollar}([0-9]+(?:\.[0-9]+)?) at (.+?) on (.+?), "
        r"then the market resolves to Yes\.",
        primary,
    )
    if not match or match[1] != match[3] or D(match[2]) != strike:
        raise ValueError("Unrecognized hourly settlement wording or strike")
    # Hourly rules specify a simple mean, with no rounding of that mean. Unlike
    # 15m contracts, comparison is strict at the published decimal strike.
    expected_secondary = {
        "ETHD": "Not all cryptocurrency price data is the same. While checking a source like Google or Coinbase may help guide your decision, the price used to determine this market is based on CF Benchmarks' corresponding Real Time Index (RTI). At the last minute before expiration, 60 RTI prices are collected. The official and final value is the average of these prices.",
        "HYPED": "Not all cryptocurrency price data is the same. While checking a source like Google or Coinbase may help guide your decision, the price used to determine this market is based on CF Benchmarks' corresponding Real Time Index (RTI). At the last minute before expiration, 60 RTI prices are collected. The official and final value is the average of these prices.",
        "XRPD": "The market resolves based on a simple average of the CF Benchmarks index for the 60 seconds prior to the specified time. The price must meet the criterion at exactly the specified time on the target date. If no data is available or incomplete at the expiration time, affected strikes resolve to No. For cryptocurrencies with multiple versions, the Exchange will specify which version or ticker is being tracked. The CF Benchmarks Real-Time Index provides continuous pricing data for major cryptocurrencies.",
    }
    if secondary != expected_secondary[asset.symbol]:
        raise ValueError("Unrecognized hourly secondary rules")
    end, start = timestamp(raw["close_time"]), timestamp(raw["open_time"])
    if end % 3600 != 0 or start >= end:
        raise ValueError("Hourly close must be on the hour")
    rule_time = None
    for fmt in (
        "%I %p %Z on %b %d, %Y",
        "%I:%M %p %Z on %b %d, %Y",
        "%I %p %Z on %B %d, %Y",
        "%I:%M %p %Z on %B %d, %Y",
    ):
        for zone in ("EDT", "EST"):
            text = match[3] + " on " + match[4]
            try:
                dt = datetime.strptime(text.replace(zone, "UTC"), fmt).replace(
                    tzinfo=ZoneInfo("America/New_York")
                )
                if zone in text and dt.tzname() == zone:
                    rule_time = dt.timestamp()
            except ValueError:
                pass
    if (
        rule_time != end
        or datetime.fromtimestamp(end, ZoneInfo("America/New_York")).strftime("%y%b%d%H").upper()
        != event.split("-")[1]
    ):
        raise ValueError("Hourly rule/event/metadata time conflict")
    if not any(s.get("name") == "CF Benchmarks" for s in series.get("settlement_sources", [])):
        raise ValueError("Settlement source missing")
    # Settlement timer is the exchange payout delay, not the averaging window.
    # Captured ETH is 60 seconds and XRP is 1800; the rules above establish 60 samples.
    if raw.get("notional_value_dollars") != "1.0000":
        raise ValueError("Unexpected hourly payout")
    bands = raw.get("price_ranges", [])
    if not bands or any(not 0 <= D(b["start"]) < D(b["end"]) <= 1 or D(b["step"]) != D(".01") for b in bands):
        raise ValueError("Unverified hourly price grid")
    spec = SettlementSpecification(
        asset.reference_source,
        asset.index,
        float(strike),
        end - 60,
        end,
        ">",
        round_digits=asset.round_digits,
        rounding="unrounded",
        rules_hash=hashlib.sha256((primary + secondary).encode()).hexdigest(),
    )
    return Market(
        raw["ticker"],
        event,
        raw.get("market_id", raw["ticker"]),
        raw["title"],
        start,
        end,
        timestamp(raw["expiration_time"]),
        raw["status"],
        int(raw["exchange_index"]),
        spec,
        bands,
        raw,
    )


def publish_candidates(engine, published):
    """Publish a complete ladder pass atomically, including lost-signal invalidations.

    The small candidate index lets the executor cycle every qualifying strike;
    it never authorizes a buy without rereading that ticker's fresh decision.
    """
    changes, candidates, current = [], {}, {}
    for ticker, decision in engine.latest.items():
        eligible = decision.get("decision") == "TRADE_CANDIDATE"
        previous = published.get(ticker, {})
        if not eligible and not previous.get("eligible"):
            continue
        since = previous.get("since") if previous.get("eligible") else decision["timestamp"]
        current[ticker] = dict(timestamp=decision["timestamp"], eligible=eligible, since=since)
        if eligible:
            candidates[ticker] = dict(
                since=since, volume=float(engine.markets[ticker].raw.get("volume_fp") or 0)
            )
        if current[ticker] != previous:
            changes.append((ticker, decision))
    if not changes and current == published:
        return False
    with engine.store.transaction():
        for ticker, decision in changes:
            engine.store.publish_record(
                "evaluation",
                decision,
                engine.run_id,
                engine.mode,
                decision["timestamp"],
                ticker,
                engine._last_op.get(ticker, ""),
                key=f"evaluation:{engine.run_id}:{ticker}",
            )
        engine.store.publish_market_display(candidates, f"hourly_candidates:{engine.run_id}")
    published.clear()
    published.update(current)
    return True


def entry_obligations(path, series):
    """Read filled/pending buys conservatively; a read failure must abort rotation."""
    import json
    import sqlite3
    from pathlib import Path

    from .manual_trading import UNRESOLVED

    path = Path(path)
    if not path.exists():
        return set()
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5) as db:
        rows = db.execute(
            "SELECT body FROM manual_orders WHERE json_extract(body, '$.request.ticker') >= ? "
            "AND json_extract(body, '$.request.ticker') < ?",
            (series + "-", series + "."),
        ).fetchall()
    return {
        row["request"]["ticker"]
        for (body,) in rows
        for row in [json.loads(body)]
        if row["request"]["action"] == "buy"
        and (row["state"] in UNRESOLVED or D((row.get("exchange_order") or {}).get("fill_count_fp", "0")) > 0)
    }


def select_markets(engine, raws, series, now, obligations=()):
    """Rank REST quotes for subscription only; never publish them as trade signals.

    Minimize the summed probability/ask-band shortfall (both measured in dollars
    per $1 contract). Preserve ties to avoid rotating otherwise equal candidates.
    Actual entry still needs fresh sequenced books and the full engine checks.
    """
    import math

    from .domain import parse_market
    from .strategies.settlement_edge.bleep import capped_confidence, probability
    from .strategies.settlement_edge.model import features

    c = engine.config
    current = engine.hourly_watchlist or set()
    protected = (
        set(obligations)
        | set(engine.executor.positions)
        | {t for t, order in engine.executor.orders.items() if order.active}
    )
    reference = engine.ticks[-1] if engine.ticks else None
    reference_fresh = reference is not None and (
        0 <= now - reference.received <= c.reference_max_age
        and -c.max_clock_skew <= now - reference.source <= c.reference_max_age
    )
    f = None
    if reference_fresh:
        try:
            f = features(engine.ticks, now, c)
            if c.bleep_exchange_seed_enabled and engine.bleep_seed:
                from .bleep_seed import seeded_inputs

                f["bleep"] = seeded_inputs(
                    engine.bleep_candles, engine.bleep_seed["last_minute"], engine.ticks, now
                )
        except ValueError:
            pass
    ranked, pinned, evidence = [], [], {}
    for raw in raws:
        ticker = raw["ticker"]
        if ticker in protected:
            pinned.append(raw)
            evidence[ticker] = dict(reason="FILLED_OR_PENDING")
            continue
        try:
            market = parse_market(raw, series)
            if not market.tradable(now):
                continue
            side = market.spec.favored(reference.price) if reference_fresh else None
            quotes = {}
            for candidate in ("yes", "no"):
                bid, ask = (float(raw.get(candidate + key) or 0) for key in ("_bid_dollars", "_ask_dollars"))
                if all(math.isfinite(v) for v in (bid, ask)) and 0 <= bid <= ask <= 1 and ask > 0:
                    quotes[candidate] = (bid, ask)
            # Startup has no causal reference yet: bootstrap subscriptions with
            # quote-only ranking, then rerank with the model after reference arrives.
            if side is None and quotes:
                side = max(quotes, key=lambda k: sum(quotes[k]))
            bid, ask = quotes[side]
            confidence = capped_confidence(1.0, bid, ask, c.asset)
            basis = "QUOTE_ONLY_WARMUP"
            if f is not None:
                try:
                    p = probability(market.spec, engine.ticks, now, f, c)
                    confidence = capped_confidence(p["p_" + side], bid, ask, c.asset)
                    basis = "MODEL_AND_REST_QUOTES"
                except ValueError:
                    pass
            floor = c.probability_floor(remaining=market.close_time - now)
            gap = max(0, floor - confidence) + max(0, c.min_entry_price - ask, ask - c.max_entry_price)
            volume = float(raw.get("volume_fp") or 0)
            if not math.isfinite(volume):
                volume = 0
            evidence[ticker] = dict(basis=basis, side=side, ask=ask, confidence=confidence, shortfall=gap)
            ranked.append(((gap, ticker not in current, -volume, ticker), raw))
        except (ValueError, KeyError, TypeError, ArithmeticError):
            continue
    ranked.sort(key=lambda item: item[0])
    chosen = pinned + [raw for _, raw in ranked[: max(0, 2 - len(pinned))]]
    return chosen, {raw["ticker"]: evidence[raw["ticker"]] for raw in chosen}
