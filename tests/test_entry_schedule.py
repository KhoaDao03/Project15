from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from test_live_automation import live  # noqa: F401
from test_manual_trading import venue  # noqa: F401
from test_stop_worker import held_position

from btc15.entry_schedule import entry_blackout


@pytest.mark.parametrize('month,day', [(1, 5), (7, 6)])
@pytest.mark.parametrize('offset,start,end', [
    (0, '11:45', '13:00'), (1, '11:45', '13:00'),
    (2, '11:45', '13:00'), (3, '11:45', '13:00'), (4, '11:45', '13:00'),
    (1, '20:00', '21:15'), (3, '19:45', '21:00'),
    (5, '11:15', '12:30'), (6, '11:15', '12:30'),
])
def test_boundaries_in_winter_and_summer(month, day, offset, start, end):
    def stamp(hm):
        return datetime.fromisoformat(f'2026-{month:02}-{day+offset:02}T{hm}').replace(
            tzinfo=ZoneInfo('America/New_York')).timestamp()
    a, b = stamp(start), stamp(end)
    assert entry_blackout(a-0.001) is None
    assert entry_blackout(a)
    assert entry_blackout(b-0.001)
    assert entry_blackout(b) is None


@pytest.mark.parametrize('stamp', ['2026-10-07T20:30:00-04:00',
                                  '2026-10-09T20:30:00-04:00',
                                  '2026-10-10T12:45:00-04:00',
                                  '2026-10-05T11:30:00-04:00'])
def test_windows_do_not_leak_to_other_days(stamp):
    assert entry_blackout(datetime.fromisoformat(stamp).timestamp()) is None


@pytest.mark.parametrize('live', ['BTC', 'ETH', 'SOL', 'XRP', 'BNB', 'HYPE', 'DOGE'], indirect=True)
def test_all_crypto_assets_block_before_health_or_backlog_bypass(live, monkeypatch):
    worker, state, manual, control, data, clock = live
    monkeypatch.setattr('btc15.live_automation.entry_blackout', lambda now: 'Weekdays 11:45–13:00 ET')
    with pytest.raises(HTTPException, match='ENTRY_TIME_BLACKOUT'):
        worker.entry(control, clock[0])
    assert not state['posts']


@pytest.mark.anyio
async def test_window_starting_during_preflight_prevents_post(live, monkeypatch):
    worker, state, manual, control, data, clock = live
    blocked = False
    monkeypatch.setattr('btc15.live_automation.entry_blackout', lambda now: 'Tuesday 20:00–21:15 ET' if blocked else None)
    original = manual.market
    async def change(*args):
        nonlocal blocked
        result = await original(*args)
        blocked = True
        return result
    monkeypatch.setattr(manual, 'market', change)
    await worker.step_market(control)
    assert not state['posts']
    assert manual.rows()[0]['state'] == 'rejected'
    assert 'ENTRY_TIME_BLACKOUT' in manual.rows()[0]['message']


@pytest.mark.anyio
async def test_stop_exit_still_submits_during_blackout(live, monkeypatch):
    worker, state, manual, control, data, clock = await held_position(live)
    monkeypatch.setattr('btc15.live_automation.entry_blackout', lambda now: 'Weekdays 11:45–13:00 ET')
    data['bid'] = .50
    assert worker.detect_stops()
    await worker.step_market(control, stops_only=True)
    assert len(state['posts']) == 2
    assert state['posts'][-1]['reduce_only']


@pytest.mark.parametrize('live', ['GOLD', 'SILVER', 'WTI'], indirect=True)
def test_commodities_ignore_crypto_blackout(live, monkeypatch):
    worker, state, manual, control, data, clock = live
    monkeypatch.setattr('btc15.live_automation.entry_blackout', lambda now: 'Weekdays 11:45–13:00 ET')
    side, limit, decision = worker.entry(control, clock[0])
    assert side == 'yes'
