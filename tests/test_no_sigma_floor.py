from dataclasses import replace

import pytest
from test_sustained_lead import reference, setup_engine


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
