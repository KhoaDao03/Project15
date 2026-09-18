"""Expected values are specified independently from captured official observations."""

import asyncio
import copy
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import httpx
import pytest

from btc_probability.adapters import KalshiReader
from btc_probability.contracts import discover_spec, timestamp
from btc_probability.engine import Engine
from btc_probability.reconciliation import reconcile, reconstruct
from btc_probability.reference import ReferenceHistory
from btc_probability.schema import Config, ContractSpec, Reference

from .test_models import spec

ROOT = Path(__file__).parent / "fixtures" / "settlement_audit_20260917"


def read(name):
    return json.loads((ROOT / name).read_text())


def inputs():
    series, market = read("series.json")["series"], read("market.json")["market"]
    docs = {
        r["name"]: dict(
            url=r["url"], sha256=r["sha256"], status=r["status"], received_time=timestamp(r["retrieved_at"])
        )
        for r in read("manifest.json")
        if r["name"] in ("contract_terms_url", "contract_url")
    }
    return series, market, docs


def parsed():
    s, m, d = inputs()
    return discover_spec(s, m, 1789627740, documents=d)


def test_captured_contract_independent_expectations():
    s = parsed()
    assert s.market_ticker == "KXBTC15M-26SEP170300-00"
    assert s.target == "76492.62"
    assert s.reference_index == "BRTI"
    assert (s.comparison, s.equality_yes, s.decimal_places, s.rounding) == (">=", True, 2, None)
    assert s.open_time == 1789627500
    assert s.observation_end_time == s.trading_close_time == 1789628400
    assert s.sample_times == tuple(range(1789628340, 1789628400))
    assert s.opening_sample_times == tuple(range(1789627440, 1789627500))
    assert s.expected_expiration_time == 1789628700
    assert s.administrative_expiration_time == 1790233200
    assert s.unresolved == ("ROUNDING_TIE_UNSPECIFIED",)
    assert not s.verified
    assert ContractSpec.from_dict(asdict(s)) == s


def test_manifest_hashes():
    for row in read("manifest.json"):
        assert hashlib.sha256((ROOT / row["file"]).read_bytes()).hexdigest() == row["sha256"]


@pytest.mark.parametrize(
    "field,value,blocker",
    [
        ("rules_primary", "new terms", "PRIMARY_RULES_CHANGED"),
        ("rules_secondary", "new rounding", "SECONDARY_RULES_CHANGED"),
        ("strike_type", "greater", "COMPARISON_CONFLICT"),
        ("floor_strike", None, "MISSING_TARGET"),
        ("custom_strike", {"round_digits": "3"}, "PRECISION_METADATA_CONFLICT"),
        ("close_time", "2026-09-17T07:05:00Z", "RULE_METADATA_TIME_CONFLICT"),
        ("is_provisional", True, "CONTINGENCY_OR_PROVISIONAL_RESULT"),
    ],
)
def test_changed_semantics_block(field, value, blocker):
    s, m, d = inputs()
    m[field] = value
    result = discover_spec(s, m, 1789627740, documents=d)
    assert blocker in result.unresolved and not result.verified


def test_documents_stale_changed_and_price_ticks_not_rounding():
    s, m, d = inputs()
    assert "RULE_DOCUMENT_CHANGED_OR_UNAVAILABLE" in discover_spec(s, m, 1789629000, documents=d).unresolved
    d["contract_url"]["sha256"] = "changed"
    assert "RULE_DOCUMENT_CHANGED_OR_UNAVAILABLE" in discover_spec(s, m, 1789627740, documents=d).unresolved
    s, m, d = inputs()
    m["price_ranges"] = [{"step": "0.0001"}]
    assert discover_spec(s, m, 1789627740, documents=d).rounding is None
    s["settlement_sources"] = [{"name": "Coinbase"}]
    assert "REFERENCE_SOURCE_CHANGED" in discover_spec(s, m, 1789627740, documents=d).unresolved
    with pytest.raises(ValueError, match="Timezone"):
        timestamp("2026-09-17T07:00:00")


def test_published_value_reconciliation_not_just_yes_no():
    c = read("window_0645.json")
    r = reconcile(c, 1789627600)
    assert r["settlement_window"]["count"] == 60
    assert r["settlement_window"]["mean"] == "76492.62016666666666666666667"
    assert r["settlement_window"]["rounded"] == "76492.62"
    assert r["close_inclusive_helper"]["mean"] == "76492.46216666666666666666667"
    assert r["close_inclusive_helper"]["rounded"] == "76492.46"
    assert r["published"]["matches_settlement_value"]
    assert r["published"]["finalized"]
    assert not r["published"]["matches_close_inclusive_value"]
    assert all(v["matches_reconstruction"] for v in r["provider"].values())
    assert read("final_market_0645.json")["market"]["expiration_value"] == "76492.62"
    # Official target 76349.87: either reconstructed window says YES, hiding the numeric error.
    assert float(r["settlement_window"]["mean"]) > 76349.87
    assert float(r["close_inclusive_helper"]["mean"]) > 76349.87
    # Counterfactual target separates averaging from a last-price shortcut.
    last = next(e for e in c["references"] if e["data"]["normalized"]["source_time"] == 1789627499)
    assert float(last["data"]["normalized"]["value"]) < 76490 < float(r["settlement_window"]["mean"])


def test_boundaries_partial_duplicates_missing_late_and_conflicts():
    c = read("window_0645.json")
    events = c["references"]
    end = 1789627500
    early = reconcile(c, end - 30)
    assert early["settlement_window"]["count"] == 31
    assert early["settlement_window"]["mean"] is None
    assert early["published"]["status"] == "NOT_YET_AVAILABLE"
    assert not early["provider"]
    assert reconstruct(events + events, end - 60, end, end)["count"] == 60
    removed = [e for e in events if e["data"]["normalized"]["source_time"] != end - 60]
    assert reconstruct(removed, end - 60, end, end)["missing"] == [end - 60]
    late = copy.deepcopy(events)
    item = next(e for e in late if e["data"]["normalized"]["source_time"] == end - 60)
    item["received_time"] = end + 10
    item["data"]["normalized"]["received_time"] = end + 10
    assert reconstruct(late, end - 60, end, end)["count"] == 59
    assert reconstruct(late, end - 60, end, end + 10)["count"] == 60
    conflicting = copy.deepcopy(item)
    conflicting["data"]["normalized"]["value"] = "1"
    assert reconstruct(late + [conflicting], end - 60, end, end + 10)["conflicts"] == [end - 60]
    # At T-60 exactly the first sample is eligible; T is never a settlement sample.
    assert reconstruct(events, end - 60, end, end - 60)["count"] == 1
    assert reconstruct(events, end - 60, end, end - 61)["count"] == 0
    assert reconstruct(events, end - 60, end, end - 1)["count"] == 60


def test_rounding_order_equality_and_unresolved_tie():
    # Explicit synthetic policy tests mechanics, not a claim about the real tie convention.
    s = replace(spec(), target="100.01", rounding="ROUND_HALF_UP", decimal_places=2)
    assert s.yes("100.006")  # round average before comparison
    assert s.yes("100.01")  # equality wins
    assert not s.yes("100.004")
    events = [
        dict(received_time=t, data={"normalized": asdict(Reference(v, t, t))})
        for t, v in enumerate(["100.004", "100.005"])
    ]
    assert reconstruct(events, 0, 2, 2)["rounded"] == "100.00"  # rounding samples first would give 100.01
    events[0]["data"]["normalized"]["value"] = "100.005"
    result = reconstruct(events, 0, 2, 2)
    assert result["status"] == "ROUNDING_TIE_UNSPECIFIED" and result["rounded"] is None
    assert result["adjacent_cents"] == ["100.00", "100.01"]


def test_crosscheck_causality_and_distinct_aggregates():
    c = read("window_0645.json")
    h = ReferenceHistory()
    for e in c["references"]:
        h.add(Reference(**e["data"]["normalized"]))
    s = replace(parsed(), observation_end_time=1789627500, sample_times=tuple(range(1789627440, 1789627500)))
    event = next(e for e in c["references"] if e["data"]["normalized"]["source_time"] == 1789627500)
    agg = event["data"]["raw"]["msg"]["avg_60s_data"]
    kwargs = dict(field="avg_60s_data", source_time=1789627500, received_time=1789627501)
    assert h.crosscheck(s, agg, 1789627500, **kwargs) == "NOT_YET_AVAILABLE"
    assert h.crosscheck(s, agg, 1789627501, **kwargs) == "MATCH"
    assert h.crosscheck(s, agg, 1789627501) == "DIFFERENT_WINDOW_SEMANTICS"
    assert h.crosscheck(s, dict(agg, window_size=59), 1789627501, **kwargs) == "AGGREGATE_COUNT_MISMATCH"


def test_research_continues_without_primary_and_raw_calibration_separate():
    s = parsed()
    now = 1789627740
    e = Engine(Config(provider="fixed", paths=100), "live")
    e.contracts[s.market_ticker] = s
    e.contract_ids[s.market_ticker] = 1
    e.history.add(Reference("76490", now, now))
    f = e.forecast(s.market_ticker, now)
    assert f.verification_readiness == "UNRESOLVED" and f.input_readiness == "VALID"
    assert f.research_diagnostic and f.raw_p_yes is f.display_probability is f.terminal is None
    assert f.calibrated_p_yes is None and f.model_readiness == "BLOCKED"
    assert f.research_settlement["policy"] == "user-model-lean-v1"
    assert not f.research_settlement["verified_settlement"]
    # Separately verified synthetic mechanics can produce an explicitly uncalibrated raw estimate.
    synthetic = spec()
    e = Engine(Config(provider="fixed", paths=100), "synthetic")
    e.contracts[synthetic.market_ticker] = synthetic
    e.contract_ids[synthetic.market_ticker] = 1
    e.history.add(Reference("100", 0, 0))
    f = e.forecast(synthetic.market_ticker, 0)
    assert f.verification_readiness == "VERIFIED" and f.model_readiness == "RAW_UNCALIBRATED"
    assert f.raw_p_yes is not None and f.calibrated_p_yes is None


def test_rule_document_capture_uses_actual_bytes():
    asyncio.run(capture_documents())


async def capture_documents():
    async def handler(request):
        assert request.method == "GET" and request.url.host == "assets.kalshi.com"
        return httpx.Response(200, content=b"changed official bytes")

    reader = KalshiReader(transport=httpx.MockTransport(handler))
    try:
        docs = await reader.rule_documents()
        assert len(docs) == 2
        assert all(
            d["sha256"] == hashlib.sha256(b"changed official bytes").hexdigest() for d in docs.values()
        )
        assert all(d["content_base64"] for d in docs.values())
    finally:
        await reader.close()


def test_second_actual_processed_market_and_opening_target_reconcile():
    capture = read("window_0700.json")
    result = reconcile(capture, 1789628500)
    assert result["settlement_window"]["mean"] == "76406.24916666666666666666667"
    assert result["settlement_window"]["rounded"] == "76406.25"
    assert result["close_inclusive_helper"]["mean"] == "76406.23216666666666666666667"
    assert result["close_inclusive_helper"]["rounded"] == "76406.23"
    assert result["published"]["matches_settlement_value"]
    assert result["published"]["lifecycle"] == "determined"
    assert not result["published"]["finalized"]
    assert not result["published"]["matches_close_inclusive_value"]
    assert all(v["matches_reconstruction"] for v in result["provider"].values())
    assert read("final_market_0700.json")["market"]["expiration_value"] == "76406.25"
    # This market's opening target equals the independently reconstructed prior window.
    prior = reconcile(read("window_0645.json"), 1789627600)
    assert parsed().target == prior["settlement_window"]["rounded"]


def test_outcome_reducer_records_numeric_reconciliation():
    capture = read("window_0700.json")
    engine = Engine(Config(), "live")
    s = parsed()
    engine.contracts[s.market_ticker] = s
    for event in capture["references"]:
        engine.apply(dict(event, kind="reference"))
    assert engine.last_reconciliation is None
    engine.apply(dict(capture["official_result_event"], kind="outcome"))
    assert engine.last_reconciliation["published"]["matches_settlement_value"]
    assert engine.last_reconciliation["as_of"] == capture["official_result_event"]["received_time"]
