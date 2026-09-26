import json

import pytest

from btc15.runner import display_reference_tick


def reference(index='HYPEUSD_RTI', source=100000, value='91.7391'):
    return dict(type='cfbenchmarks_value', msg=dict(
        index_id=index, data=json.dumps(dict(id=index, type='value', time=source, value=value))))


def test_official_one_hz_display_without_five_hz():
    tick = display_reference_tick(reference(), 'HYPEUSD_RTI', 100.1)
    assert tick == dict(value='91.7391', source_ts_ms=100000, received=100.1,
                        source_channel='cfbenchmarks_value')


def test_faster_reference_not_replaced_by_older_one_hz_sample():
    fast = dict(type='cfbenchmarks_value_5hz', msg=dict(
        index_id='HYPEUSD_RTI', value_usd='91.75', source_ts_ms=100200))
    previous = display_reference_tick(fast, 'HYPEUSD_RTI', 100.3)
    assert display_reference_tick(reference(), 'HYPEUSD_RTI', 100.4, previous) is previous
    updated = display_reference_tick(reference(source=101000), 'HYPEUSD_RTI', 101.1, previous)
    assert updated['source_ts_ms'] == 101000


@pytest.mark.parametrize('payload', [
    reference(index='BNBUSD_RTI'), reference(source=97000), reference(source=103000),
    reference(value='nan'), reference(value='inf'), reference(value='0'),
    dict(type='cfbenchmarks_value', msg=dict(index_id='HYPEUSD_RTI', data='broken')),
    dict(type='cfbenchmarks_value', msg=dict(index_id='HYPEUSD_RTI', data='[]')),
    dict(type='cfbenchmarks_value', msg=dict(index_id='HYPEUSD_RTI', data=json.dumps(
        dict(id='BNBUSD_RTI', type='value', time=100000, value='700')))),
])
def test_invalid_display_sample_cannot_refresh_previous_value(payload):
    previous = display_reference_tick(reference(), 'HYPEUSD_RTI', 100)
    assert display_reference_tick(payload, 'HYPEUSD_RTI', 100.1, previous) is previous
    assert display_reference_tick(payload, 'HYPEUSD_RTI', 100.1) is None
