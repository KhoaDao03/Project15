from dataclasses import replace

import pytest
from test_live_automation import live  # noqa: F401
from test_manual_trading import venue  # noqa: F401
from test_stop_worker import held_position
from test_exit_execution_v2 import held, quote, sells

from btc15.config import Strategy


@pytest.mark.anyio
@pytest.mark.parametrize('bid', [0, .01, .40, .55])
async def test_disabled_live_stop_holds_even_at_zero(live, bid):
    worker, state, manual, control, data, clock = await held_position(live)
    control['stop_price'] = 0
    worker.write(control)
    data['bid'] = bid
    assert worker.detect_stops() == []
    await worker.step_market(control)
    assert len(state['posts']) == 1
    assert not worker.control(control['ticker']).get('exit_reason')


@pytest.mark.parametrize('side', ['yes', 'no'])
def test_disabled_paper_stop_holds_at_zero(store, market, now, side):
    c = replace(Strategy.load('config/settlement-edge-active-paper.json'),
                fixed_stop_price=0, stop_multiplier=0, take_profit=None)
    ex = held(store, market, now, c, side)
    for n, bid in enumerate(['.40', '.01', '0'], 1):
        ex.monitor(market, quote(now+n, [(bid, 10)], side),
                   {'conservative_'+side: 0}, now+n, 'hold')
    assert not store.list(kind='exit_intent') and not sells(store)
