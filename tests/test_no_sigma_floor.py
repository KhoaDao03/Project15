from dataclasses import replace

import pytest
from test_sustained_lead import reference, setup_engine

from btc15.config import Strategy


@pytest.mark.parametrize("remaining", [300, 40])
@pytest.mark.parametrize("disabled", [True, False])
def test_small_lead_can_confirm_only_when_floor_disabled(
    store, market, config, monkeypatch, remaining, disabled
):
    import btc15.engine as module

    original = module.lead_evidence

    def small_lead(*args, **kwargs):
        return {**original(*args, **kwargs), "lead_sigma": 0.01}

    monkeypatch.setattr(module, "lead_evidence", small_lead)
    c = replace(
        config,
        min_lead_sigma=0 if disabled else 1,
        late_min_lead_sigma=0 if disabled else 2,
        lead_confirmation_samples=3,
        late_lead_confirmation_samples=2,
        entry_probability_deductions=False,
    )
    start = market.close_time - remaining
    e, price = setup_engine(store, market, c, start)
    count = 2 if remaining == 40 else 3
    for i in range(count - 1):
        reference(e, market, start + i, price)
        assert not e.executor.orders
    reference(e, market, start + count - 1, price)
    assert bool(e.executor.orders) == disabled
    codes = {r["code"] for r in e.latest[market.ticker]["reasons"]}
    assert ("SETTLEMENT_LEAD" in codes) != disabled


def test_all_active_presets_disable_only_sigma_floor():
    for asset in ("active", "eth", "sol", "xrp"):
        c = Strategy.load(f"config/settlement-edge-{asset}-paper.json")
        assert c.min_lead_sigma == c.late_min_lead_sigma == 0
        assert c.sustained_lead_enabled and c.late_entry_enabled
        assert c.confirmation_count() == 1 and c.confirmation_count(True) == 1
        assert c.bleep_probability_blend_enabled and c.min_probability == c.late_min_probability == 0.85
        assert (c.min_entry_price, c.max_entry_price, c.fixed_stop_price) == (0.85, 0.95, 0.55)
