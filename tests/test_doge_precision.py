import copy
import json
from pathlib import Path

import pytest

from btc15.domain import parse_market

FIXTURE = json.loads(Path('tests/fixtures/doge15-20260926.json').read_text())


@pytest.mark.parametrize('raw', FIXTURE['markets'])
def test_doge_preserves_published_seven_decimal_strike(raw):
    spec = parse_market(raw, FIXTURE['series']).spec
    assert spec.strike == float(raw['custom_strike']['floor_strike'])
    assert spec.strike != raw['floor_strike']
    assert spec.round_digits == 7
    assert spec.yes(raw['custom_strike']['floor_strike'])
    assert not spec.yes(str(spec.strike - .0000001))


@pytest.mark.parametrize('change', ['missing', 'invalid', 'precision', 'operator', 'conflict', 'numeric'])
def test_doge_rejects_unverified_strikes(change):
    raw = copy.deepcopy(FIXTURE['markets'][0])
    if change == 'missing':
        del raw['custom_strike']['floor_strike']
    elif change == 'invalid':
        raw['custom_strike']['floor_strike'] = 'NaN'
    elif change == 'precision':
        raw['custom_strike']['floor_strike'] += '1'
    elif change == 'operator':
        raw['custom_strike']['strike_type'] = 'less'
    elif change == 'conflict':
        raw['floor_strike'] += .000001
    else:
        raw['rules_primary'] = raw['rules_primary'].replace(
            'the simple average of the sixty seconds of CF Benchmarks\' DOGEUSDRTI before 3:45 PM EDT on September 26, 2026',
            '0.097146')
    with pytest.raises(ValueError):
        parse_market(raw, FIXTURE['series'])


def test_official_dashboard_snapshot_preserves_doge_strike(store, monkeypatch):
    from fastapi.testclient import TestClient

    from btc15 import dashboard
    from btc15.config import Strategy

    class Client:
        def __init__(self, settings):
            pass

        async def discover(self):
            return FIXTURE['series'], FIXTURE['markets']

        async def close(self):
            pass

    monkeypatch.setattr(dashboard, 'KalshiClient', Client)
    with TestClient(dashboard.create_app(store, config=Strategy(asset='DOGE'))) as client:
        data = client.get('/api/official-markets').json()
    assert data['error'] is None
    for actual, raw in zip(data['markets'], FIXTURE['markets']):
        assert actual['floor_strike'] == float(raw['custom_strike']['floor_strike'])
