"""Narrow parser for CF Benchmarks ETH/XRP hourly above/below ladders."""

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
    index = {"ETHD": "Ethereum Real-Time Index (ERTI)", "XRPD": "Ripple-Dollar Real Time Index (XRPUSD_RTI)"}[
        asset.symbol
    ]
    match = re.fullmatch(
        rf"If the simple average of the sixty seconds of CF Benchmarks' {re.escape(index)} "
        r"before (.+?) is above ([0-9]+(?:\.[0-9]+)?) at (.+?) on (.+?), "
        r"then the market resolves to Yes\.",
        primary,
    )
    if not match or match[1] != match[3] or D(match[2]) != strike:
        raise ValueError("Unrecognized hourly settlement wording or strike")
    # Hourly rules specify a simple mean, with no rounding of that mean. Unlike
    # 15m contracts, comparison is strict at the published decimal strike.
    expected_secondary = {
        "ETHD": "Not all cryptocurrency price data is the same. While checking a source like Google or Coinbase may help guide your decision, the price used to determine this market is based on CF Benchmarks' corresponding Real Time Index (RTI). At the last minute before expiration, 60 RTI prices are collected. The official and final value is the average of these prices.",
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
