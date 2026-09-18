"""Reviewed KXBTC15M family profile. Known facts do not resolve unspecified half ties.

Evidence and numeric counterexamples: SETTLEMENT_VERIFICATION.md. The legacy
close-inclusive feed-helper schedule is NOT this contract's supported schedule.
"""

import hashlib
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .schema import decimal, digest

PROFILE_ID = "kxbtc15m-before-boundary-v1-20260917"
DOCUMENTS = {
    "contract_terms_url": {
        "url": "https://assets.kalshi.com/contract_terms/CRYPTO.pdf",
        "sha256": "fde90b9c0825df277b0b2b2be6239af221eafd01a624c1b2d3a9eaff2d6fe75c",
    },
    "contract_url": {
        "url": "https://assets.kalshi.com/regulatory/product-certifications/CRYPTO.pdf",
        "sha256": "4841dd60f533d857c58e0c338c39d5c1306282c6aa54751439b032d933b4f8a1",
    },
}
SECONDARY_HASH = "05314f789a77296c55d1d390ea32bb4642197cc012055978c04e58c7dd765ad4"
PRIMARY = re.compile(
    r"If the simple average of the sixty seconds of CF Benchmarks' BRTI before (.+?) "
    r"is at least the simple average of the sixty seconds of CF Benchmarks' BRTI before (.+?), "
    r"then the market resolves to Yes\."
)


def rule_time(text):
    """Reuse the legacy parser's DST validation, limited to this reviewed template."""
    for fmt in ("%I:%M %p %Z on %b %d, %Y", "%I:%M %p %Z on %B %d, %Y"):
        for zone in ("EDT", "EST"):
            try:
                dt = datetime.strptime(text.replace(zone, "UTC"), fmt)
                dt = dt.replace(tzinfo=ZoneInfo("America/New_York"))
                if zone in text and dt.tzname() == zone:
                    return dt.timestamp()
            except ValueError:
                pass
    raise ValueError("RULE_TIMESTAMP_UNSUPPORTED")


def analyze(series, market, received, documents):
    """Recognize a reviewed template and check dynamic values, never guess new wording."""
    from .contracts import timestamp

    blockers, checks = [], {}

    def check(name, condition, code):
        checks[name] = "verified" if condition else "unresolved"
        if not condition:
            blockers.append(code)

    check(
        "family",
        series.get("ticker") == "KXBTC15M"
        and series.get("frequency") == "fifteen_min"
        and market.get("event_ticker", "").startswith("KXBTC15M-")
        and re.fullmatch(re.escape(market.get("event_ticker", "")) + r"-\d+", market.get("ticker", ""))
        and market.get("market_type") == "binary",
        "UNSUPPORTED_CONTRACT_FAMILY",
    )
    check(
        "reference",
        any(s.get("name") == "CF Benchmarks" for s in series.get("settlement_sources", [])),
        "REFERENCE_SOURCE_CHANGED",
    )
    provenance = {}
    for name, expected in DOCUMENTS.items():
        source = (documents or {}).get(name, {})
        valid = (
            series.get(name) == expected["url"]
            and source.get("url") == expected["url"]
            and source.get("sha256") == expected["sha256"]
            and source.get("status") == 200
            and 0 <= received - source.get("received_time", float("-inf")) <= 900
        )
        check(name, valid, "RULE_DOCUMENT_CHANGED_OR_UNAVAILABLE")
        provenance[name] = dict(source)
    match = PRIMARY.fullmatch(market.get("rules_primary", ""))
    check("primary_template", bool(match), "PRIMARY_RULES_CHANGED")
    check(
        "secondary_template",
        hashlib.sha256(market.get("rules_secondary", "").encode()).hexdigest() == SECONDARY_HASH,
        "SECONDARY_RULES_CHANGED",
    )
    start = end = None
    if match:
        try:
            end, start = rule_time(match[1]), rule_time(match[2])
        except ValueError:
            blockers.append("RULE_TIMESTAMP_UNSUPPORTED")
    check(
        "observation_times",
        start is not None
        and end is not None
        and end - start == 900
        and end % 900 == 0
        and start == timestamp(market["open_time"])
        and end == timestamp(market["close_time"]),
        "RULE_METADATA_TIME_CONFLICT",
    )
    check("comparison", market.get("strike_type") == "greater_or_equal", "COMPARISON_CONFLICT")
    target = market.get("floor_strike")
    check("target", target is not None and decimal(target) > 0, "MISSING_TARGET")
    check("payout", decimal(market.get("notional_value_dollars", "0")) == 1, "UNSUPPORTED_PAYOUT")
    # Precision comes from the exact secondary rule template, not the price grid or this helper field.
    helper = market.get("custom_strike", {}).get("round_digits")
    check(
        "precision_metadata_compatible", helper is None or str(helper) == "2", "PRECISION_METADATA_CONFLICT"
    )
    check(
        "normal_resolution",
        not market.get("early_close_condition") and not market.get("is_provisional"),
        "CONTINGENCY_OR_PROVISIONAL_RESULT",
    )
    checks.update(
        sample_schedule="verified" if checks["observation_times"] == "verified" else "unresolved",
        rounding_precision="verified" if checks["secondary_template"] == "verified" else "unresolved",
        rounding_tie="unresolved",
        calibration="not_established",
    )
    # Nearest-cent wording supplies precision/order, but no authoritative half-cent convention.
    blockers.append("ROUNDING_TIE_UNSPECIFIED")
    return dict(
        profile_id=PROFILE_ID,
        profile_hash=digest(
            dict(
                id=PROFILE_ID,
                documents=DOCUMENTS,
                secondary=SECONDARY_HASH,
                boundary="[T-60,T)",
                rounding_tie=None,
            )
        ),
        blockers=sorted(set(blockers)),
        checks=checks,
        observation_start=start,
        observation_end=end,
        documents=provenance,
        sample_boundary="[T-60,T)",
        supported_resolution="normal published data; contingencies require official result",
        noncritical_unresolved=["ADMINISTRATIVE_EXPIRATION_TEMPLATE_VS_METADATA"],
    )
