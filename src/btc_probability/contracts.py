"""Contract discovery delegates settlement semantics to the reviewed family profile."""

from datetime import datetime

from .schema import ContractSpec, decimal, digest


def timestamp(value):
    if isinstance(value, (float, int)):
        return float(value)
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result.timestamp()


def rules_hash(series, market):
    return digest(
        {
            "series": series["ticker"],
            "terms_url": series.get("contract_terms_url"),
            "certification_url": series.get("contract_url"),
            "rules_primary": market.get("rules_primary"),
            "rules_secondary": market.get("rules_secondary"),
        }
    )


def discover_spec(series, market, received, review=None, *, documents=None):
    from .contract_profile import analyze

    result = analyze(series, market, received, documents)
    blockers = result["blockers"][:]
    if review:
        # A free-form JSON review must not promote unresolved profile assumptions into verified mechanics.
        blockers.append("EXTERNAL_REVIEW_REQUIRES_VERSIONED_PROFILE")
    start = timestamp(market["open_time"])
    close = timestamp(market["close_time"])
    end = result["observation_end"] if result["observation_end"] is not None else close
    known_schedule = result["checks"]["observation_times"] == "verified"
    target = market.get("floor_strike")
    # Every required semantic check must clear. The current profile intentionally retains the tie blocker.
    verified = not blockers
    return ContractSpec(
        series_ticker=series["ticker"],
        market_ticker=market["ticker"],
        event_ticker=market["event_ticker"],
        target=None if target is None else str(decimal(target)),
        target_source="market.floor_strike; API minimum expiration value for YES; greater_or_equal",
        open_time=start,
        trading_close_time=close,
        observation_end_time=end,
        sample_times=tuple(end - 60 + i for i in range(60)) if known_schedule else (),
        opening_sample_times=tuple(start - 60 + i for i in range(60)) if known_schedule else (),
        reference_index="BRTI",
        comparison=">=",
        equality_yes=True,
        rounding=None,
        decimal_places=2,
        payout=market.get("notional_value_dollars", "1"),
        currency="USD",
        lifecycle=market["status"],
        rules_url=series.get("contract_terms_url", ""),
        rules_hash=rules_hash(series, market),
        metadata_received=received,
        verified=verified,
        unresolved=tuple(sorted(set(blockers))),
        version="contract-v2",
        research_tie_policy="user-model-lean-v1",
        profile_id=result["profile_id"],
        verification=result,
        administrative_expiration_time=timestamp(market["expiration_time"])
        if market.get("expiration_time")
        else None,
        expected_expiration_time=timestamp(market["expected_expiration_time"])
        if market.get("expected_expiration_time")
        else None,
        settled_at=timestamp(market["settlement_ts"]) if market.get("settlement_ts") else None,
    )
