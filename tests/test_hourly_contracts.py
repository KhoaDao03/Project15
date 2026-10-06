import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from btc15.assets import asset_spec
from btc15.config import Settings, Strategy
from btc15.domain import parse_market
from btc15.strategies.settlement_edge.bleep import SIGMA_MULTIPLIERS, capped_confidence


@pytest.fixture(params=["ETHD", "XRPD"])
def hourly(request):
    data = json.loads(Path(f"tests/fixtures/{request.param.lower()}-hourly-20261006.json").read_text())
    return (
        data["market"],
        data["series"],
        Strategy.load(f"config/settlement-edge-{request.param.lower()}-paper.json"),
    )


def test_hourly_identity_precision_and_presets(hourly):
    raw, series, c = hourly
    m = parse_market(raw, series)
    assert m.spec.strike == raw["floor_strike"] and m.spec.comparison_operator == ">"
    assert m.spec.settlement_start == m.close_time - 60
    assert len(m.spec.sample_times) == 60 and m.spec.sample_times[-1] == m.close_time
    assert not m.spec.yes(m.spec.strike)
    assert m.spec.yes(m.spec.strike + 10 ** (-c.asset_spec.round_digits - 2))
    assert m.spec.favored(m.spec.strike) == "yes"
    assert c.asset_spec.index == asset_spec(c.asset[:-1]).index
    assert c.entry_window_start == 600 and c.entry_cutoff == 60 and not c.late_entry_enabled
    assert c.early_min_probability == 0 and c.probability_floor(remaining=600) == 0.83
    assert c.fixed_stop_price == c.stop_multiplier == c.post_close_cooldown == 0
    assert c.take_profit is None and c.entry_limit_offset is None
    assert c.fixed_contracts == c.max_contracts == 10 and c.max_open_exposure == 25
    assert c.one_trade_per_market and c.bleep_safety_clamp_enabled and c.sustained_lead_enabled
    assert SIGMA_MULTIPLIERS[c.asset] == {"ETHD": 1.0, "XRPD": 1.1}[c.asset]
    premium = 0.06 if c.asset == "ETHD" else 0.10
    assert capped_confidence(0.98, 0.80, 0.82, c.asset) == pytest.approx(0.81 + premium)


@pytest.mark.parametrize(
    "change",
    [
        dict(strike_type="between"),
        dict(cap_strike=10000),
        dict(custom_strike={}),
        dict(rules_secondary="Changed rules"),
        dict(floor_strike=999),
        dict(close_time="2026-10-06T17:01:00Z"),
    ],
)
def test_hourly_parser_fails_closed(hourly, change):
    raw, series, _ = hourly
    with pytest.raises(ValueError):
        parse_market(dict(raw, **change), series)


@pytest.mark.parametrize("hours", [25, 168])
def test_hourly_long_open_time_is_allowed(hourly, hours):
    raw, s, _ = hourly
    end = datetime.fromisoformat(raw["close_time"].replace("Z", "+00:00")).timestamp()
    m = parse_market(dict(raw, open_time=datetime.fromtimestamp(end - hours * 3600, UTC).isoformat()), s)
    assert m.close_time - m.open_time == hours * 3600


@pytest.mark.parametrize("offset", [None, 0, 0.01, 0.05])
def test_optional_limit_and_identity(hourly, offset):
    _, _, c = hourly
    new = replace(c, entry_limit_offset=offset)
    assert (new.version == c.version) == (offset is None)


@pytest.mark.parametrize("offset", [-0.01, 0.051, True, "0.01", float("nan"), float("inf")])
def test_invalid_entry_offset(offset):
    with pytest.raises(ValueError):
        Strategy(entry_limit_offset=offset)


def test_existing_preset_hashes_unchanged():
    hashes = json.loads(Path("tests/fixtures/pre-hourly-config-hashes.json").read_text())
    for path, version in hashes.items():
        assert Strategy.load(path).version == version, path


@pytest.mark.anyio
async def test_only_next_hour_discovered(hourly, monkeypatch):
    from btc15.api import KalshiClient

    raw, series, c = hourly
    m = parse_market(raw, series)
    client = KalshiClient(replace(Settings(), asset=c.asset))
    monkeypatch.setattr("btc15.api.time.time", lambda: m.close_time - 300)

    async def get(path, *args):
        return {"series": series}

    async def pages(path, key, params, *args):
        if params["status"] == "open":
            yield raw
            yield dict(
                raw,
                ticker=raw["ticker"] + "OLD",
                close_time=datetime.fromtimestamp(m.close_time - 3600, UTC).isoformat(),
            )
            yield dict(
                raw,
                ticker=raw["ticker"] + "NEXT",
                close_time=datetime.fromtimestamp(m.close_time + 3600, UTC).isoformat(),
            )

    client.get = get
    client.pages = pages
    try:
        _, rows = await client.discover()
        assert [x["ticker"] for x in rows] == [m.ticker]
        monkeypatch.setattr("btc15.api.time.time", lambda: m.close_time)
        _, rows = await client.discover()
        assert rows[0]["ticker"].endswith("NEXT") and len(rows) == 1
    finally:
        await client.close()


def test_hourly_changes_only_requested_preset_fields(hourly):
    from dataclasses import asdict

    _, _, c = hourly
    original = Strategy.load(f"config/settlement-edge-{c.asset[:-1].lower()}-paper.json")
    allowed = {
        "asset",
        "entry_window_start",
        "no_new_entry",
        "late_entry_enabled",
        "early_min_probability",
        "min_probability",
        "min_entry_price",
        "max_entry_price",
        "fixed_stop_price",
        "stop_multiplier",
        "take_profit",
        "fixed_contracts",
        "max_contracts",
        "max_open_exposure",
        "one_trade_per_market",
        "post_close_cooldown",
        "bleep_safety_clamp_enabled",
        "entry_limit_offset",
    }
    for key, value in asdict(c).items():
        if key not in allowed:
            assert value == getattr(original, key), key
