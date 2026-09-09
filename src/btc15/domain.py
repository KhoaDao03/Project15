import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal


def D(x):
    return Decimal(str(x))


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result.timestamp()


@dataclass(frozen=True)
class SettlementSpecification:
    reference_source: str
    index_name: str
    strike: float
    settlement_start: float
    settlement_end: float
    comparison_operator: str
    averaging_window_seconds: int = 60
    sample_frequency: float = 1
    round_digits: int = 2
    rounding: str = "ambiguous_half_tie"
    rules_hash: str = ""

    def __post_init__(self):
        if self.reference_source != "CF Benchmarks" or self.index_name != "BRTI":
            raise ValueError("Official BTC reference required")
        if self.comparison_operator not in (">=", ">", "<=", "<"):
            raise ValueError("Unsupported comparison")
        if not math.isfinite(self.strike) or self.strike <= 0:
            raise ValueError("Invalid strike")
        if self.settlement_end - self.settlement_start != self.averaging_window_seconds:
            raise ValueError("Invalid settlement window")
        if self.sample_frequency != 1 or self.averaging_window_seconds != 60 or self.round_digits != 2:
            raise ValueError("Unverified settlement methodology")
        if self.rounding not in ("ambiguous_half_tie", "half_even", "half_up"):
            raise ValueError("Unknown rounding")

    @property
    def sample_times(self):
        return [self.settlement_start + i for i in range(1, 61)]

    def yes(self, average, rounding=None):
        r = rounding or self.rounding
        if r == "ambiguous_half_tie":
            a, b = self.yes(average, "half_even"), self.yes(average, "half_up")
            if a != b:
                raise ValueError("Ambiguous settlement rounding tie")
            return a
        value = D(average).quantize(
            D("0.01"), rounding=ROUND_HALF_EVEN if r == "half_even" else ROUND_HALF_UP
        )
        strike = D(self.strike)
        return {">=": value >= strike, ">": value > strike, "<=": value <= strike, "<": value < strike}[
            self.comparison_operator
        ]

    def favored(self, price):
        if price == self.strike:
            return None
        above = price > self.strike
        return "yes" if above == (self.comparison_operator in (">=", ">")) else "no"


@dataclass(frozen=True)
class Market:
    ticker: str
    event_ticker: str
    market_id: str
    title: str
    open_time: float
    close_time: float
    expiration_time: float
    status: str
    exchange_index: int
    spec: SettlementSpecification
    price_ranges: list
    raw: dict = field(repr=False)

    def tradable(self, now):
        return self.status == "active" and self.open_time <= now < self.close_time

    def snap(self, price, up=False):
        value = D(price)
        candidates = []
        for band in self.price_ranges:
            start, end, step = (D(band[k]) for k in ("start", "end", "step"))
            bounded = min(end, max(start, value))
            n = ((bounded - start) / step).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
            p = start + n * step
            if 0 < p < 1 and start <= p <= end and ((p >= value) if up else (p <= value)):
                candidates.append(p)
        if not candidates:
            raise ValueError("No valid executable tick")
        return float(min(candidates) if up else max(candidates))

    def valid_tick(self, price):
        return (
            any(
                D(b["start"]) <= D(price) <= D(b["end"]) and (D(price) - D(b["start"])) % D(b["step"]) == 0
                for b in self.price_ranges
            )
            and 0 < price < 1
        )


def parse_market(raw, series):
    if series.get("ticker") != "KXBTC15M" or series.get("frequency") != "fifteen_min":
        raise ValueError("Not the BTC15 series")
    if not re.fullmatch(r"KXBTC15M-[A-Z0-9]+-\d+", raw.get("ticker", "")):
        raise ValueError("Not a BTC15 market")
    if raw.get("market_type") != "binary" or not raw.get("event_ticker", "").startswith("KXBTC15M-"):
        raise ValueError("Invalid market identity")
    if raw.get("floor_strike") is None:
        raise ValueError("Strike not yet published")
    primary, secondary = raw.get("rules_primary", ""), raw.get("rules_secondary", "")
    # Deliberately narrow grammar. Changed/additional conditions require a parser review.
    pattern = (
        r"If the simple average of the sixty seconds of CF Benchmarks' BRTI before (.+?) "
        r"is (at least|greater than|less than|at most) the simple average of the sixty seconds "
        r"of CF Benchmarks' BRTI before (.+?), then the market resolves to Yes\."
    )
    match = re.fullmatch(pattern, primary)
    if (
        not match
        or "60 RTI prices are collected" not in secondary
        or "rounded to the nearest 2 decimal places" not in secondary
    ):
        raise ValueError("Unrecognized settlement wording")
    operator = {"at least": ">=", "greater than": ">", "less than": "<", "at most": "<="}[match[2]]
    expected = {">=": "greater_or_equal", ">": "greater", "<": "less", "<=": "less_or_equal"}[operator]
    if raw.get("strike_type") != expected:
        raise ValueError("Wording/strike_type conflict")
    if not any(s.get("name") == "CF Benchmarks" for s in series.get("settlement_sources", [])):
        raise ValueError("Settlement source missing")
    start, end = timestamp(raw["open_time"]), timestamp(raw["close_time"])
    if end - start != 900:
        raise ValueError("Not a 15-minute contract")
    # Verify the times in the prose, including timezone, rather than rely on the ticker.
    from zoneinfo import ZoneInfo

    def prose_time(text):
        for fmt in ("%I:%M %p %Z on %b %d, %Y", "%I:%M %p %Z on %B %d, %Y"):
            for zone in ("EDT", "EST"):
                try:
                    dt = datetime.strptime(text.replace(zone, "UTC"), fmt)
                    dt = dt.replace(tzinfo=ZoneInfo("America/New_York"))
                    if dt.tzname() == zone and zone in text:
                        return dt.timestamp()
                except ValueError:
                    pass
        raise ValueError("Unrecognized rule timestamp")

    if prose_time(match[1]) != end or prose_time(match[3]) != start:
        raise ValueError("Rule/metadata time conflict")
    bands = raw.get("price_ranges", [])
    if not bands:
        raise ValueError("Missing price grid")
    for b in bands:
        if not 0 <= D(b["start"]) < D(b["end"]) <= 1 or D(b["step"]) < D(".0001"):
            raise ValueError("Invalid price grid")
    if raw.get("notional_value_dollars") != "1.0000":
        raise ValueError("Unexpected payout")
    if str(raw.get("custom_strike", {}).get("round_digits")) != "2":
        raise ValueError("Unknown rounding precision")
    strike = float(raw["floor_strike"])
    spec = SettlementSpecification(
        "CF Benchmarks",
        "BRTI",
        strike,
        end - 60,
        end,
        operator,
        rules_hash=hashlib.sha256((primary + secondary).encode()).hexdigest(),
    )
    return Market(
        raw["ticker"],
        raw["event_ticker"],
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


@dataclass
class Book:
    yes: dict = field(default_factory=dict)
    no: dict = field(default_factory=dict)
    received: float = 0
    source_time: float = 0
    valid: bool = False

    def snapshot(self, msg, received, source_time=None):
        # Collector explicitly requests use_yes_price=true; convert NO levels once here.
        self.yes = {D(p): D(q) for p, q in (msg.get("yes_dollars_fp") or []) if D(q) > 0}
        self.no = {1 - D(p): D(q) for p, q in (msg.get("no_dollars_fp") or []) if D(q) > 0}
        self.received, self.source_time, self.valid = received, source_time or received, True
        self.validate()

    def delta(self, msg, received):
        if not self.valid:
            raise ValueError("Delta without valid snapshot")
        side = msg["side"]
        if side not in ("yes", "no"):
            raise ValueError("Unknown book side")
        book = self.yes if side == "yes" else self.no
        p = D(msg["price_dollars"]) if side == "yes" else 1 - D(msg["price_dollars"])
        q = book.get(p, D(0)) + D(msg["delta_fp"])
        if not p.is_finite() or not 0 < p < 1 or not q.is_finite():
            self.valid = False
            raise ValueError("Invalid book level")
        if q < 0:
            self.valid = False
            raise ValueError("Negative book depth")
        if q:
            book[p] = q
        else:
            book.pop(p, None)
        self.received = received
        self.source_time = msg.get("ts_ms", received * 1000) / 1000
        # The snapshot validated unchanged levels; only this level was modified.
        self.validate_spread()

    def validate(self):
        if any(
            not 0 < p < 1 or not q.is_finite() for levels in (self.yes, self.no) for p, q in levels.items()
        ):
            self.valid = False
            raise ValueError("Invalid book level")
        self.validate_spread()

    def validate_spread(self):
        if self.yes and self.no and max(self.yes) + max(self.no) >= 1:
            self.valid = False
            raise ValueError("Crossed or locked book")

    def bid(self, side):
        values = self.yes if side == "yes" else self.no
        return float(max(values)) if values else None

    def asks(self, side):
        other = self.no if side == "yes" else self.yes
        return sorted((float(1 - p), float(q)) for p, q in other.items())

    def ask(self, side):
        other = self.no if side == "yes" else self.yes
        return float(1 - max(other)) if other else None

    def summary(self):
        yb, nb, ya, na = self.bid("yes"), self.bid("no"), self.ask("yes"), self.ask("no")
        yd, nd = sum(self.yes.values()), sum(self.no.values())
        return dict(
            yes_bid=yb,
            yes_ask=ya,
            no_bid=nb,
            no_ask=na,
            spread=ya - yb if ya is not None and yb is not None else None,
            midpoint=(ya + yb) / 2 if ya is not None and yb is not None else None,
            yes_depth=float(yd),
            no_depth=float(nd),
            imbalance=float((yd - nd) / (yd + nd)) if yd + nd else 0,
            yes_top5=[(float(p), float(q)) for p, q in sorted(self.yes.items(), reverse=True)[:5]],
            no_top5=[(float(p), float(q)) for p, q in sorted(self.no.items(), reverse=True)[:5]],
        )


def order_direction(action, side, leg_price):
    if action not in ("buy", "sell") or side not in ("yes", "no"):
        raise ValueError("Invalid action/side")
    return (
        "bid" if (action == "buy") == (side == "yes") else "ask",
        float(D(leg_price) if side == "yes" else 1 - D(leg_price)),
    )


def jsonable(obj):
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(type(obj).__name__)


def dumps(obj):
    return json.dumps(obj, default=jsonable, sort_keys=True, allow_nan=False)
