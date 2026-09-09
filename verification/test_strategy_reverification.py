"""Independent review tests. All observations are synthetic; production code is unchanged."""
import copy
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from test_position_management import scenario  # noqa: F401

from btc15.config import Settings, Strategy
from btc15.dashboard import create_app
from btc15.domain import Book, D, dumps
from btc15.engine import Engine
from btc15.execution import PaperExecutor
from btc15.storage import CompactRecorder, read_events
from btc15.strategies.settlement_edge.model import Tick, probability
from btc15.strategies.settlement_edge.rules import Risk, passive_price


def initialize(store, market, now, config, mode):
    for state in ('DISCOVER_MARKET', 'VALIDATE_MARKET', 'WARMUP', 'ENTRY_WINDOW', 'EVALUATING', 'TRADE_CANDIDATE'):
        store.transition('verification', mode, market.ticker, state, now)
    return PaperExecutor(store, 'verification', mode, config)


def make_book(side, bid, ask, now, depth='100.00'):
    levels = {D(bid): D(depth)}
    other = {D(1) - D(ask): D(depth)}
    return Book(yes=levels if side == 'yes' else other,
                no=other if side == 'yes' else levels,
                received=now, source_time=now, valid=True)


@pytest.mark.parametrize('mode', ['PAPER', 'BACKTEST'])
@pytest.mark.parametrize('side', ['yes', 'no'])
@pytest.mark.parametrize('preset', ['original', 'moderate'])
@pytest.mark.parametrize('ending', ['take_profit', 'hard_stop', 'settle_win', 'settle_loss'])
def test_real_model_to_trade_history(store, raw, series, now, tmp_path, mode, side, preset, ending):
    c = Strategy() if preset == 'original' else Strategy.load('config/settlement-edge-paper-moderate.json')
    clock = [now - 302]
    e = Engine(store, c, mode, clock=(lambda: clock[0]) if mode == 'PAPER' else None,
               record_evaluations=False)
    r = copy.deepcopy(raw)
    r['title'] = 'SYNTHETIC verification fixture, not observed venue data'
    ticker = r['ticker']
    recorder = CompactRecorder(tmp_path / 'verification-inputs')
    rows = []

    def send(kind, msg, when):
        clock[0] = when
        row = dict(id=f'verify-{len(rows):05d}', received=when,
                   monotonic_ns=int((when - now + 1000) * 1e9),
                   connection_id='SYNTHETIC_VERIFICATION',
                   payload=dumps(dict(type=kind, msg=msg)))
        recorder.append_rows([row])
        rows.append(row)
        assert e.ingest(row), (kind, store.list(kind='health'))

    def quote(bid, ask, when):
        b = make_book(side, bid, ask, when)
        send('orderbook_snapshot', dict(market_ticker=ticker, ts_ms=when * 1000,
             yes_dollars_fp=[[str(p), str(q)] for p, q in b.yes.items()],
             no_dollars_fp=[[str(1-p), str(q)] for p, q in b.no.items()]), when)

    def reference(when):
        value = float(r['floor_strike']) + (200 if side == 'yes' else -200)
        send('cfbenchmarks_value', dict(index_id='BRTI', data=json.dumps(
            dict(type='value', id='BRTI', time=when * 1000, value=str(value)))), when)

    try:
        send('metadata', dict(series=series, markets=[r], clock_skew=0,
             exchange_status={'trading_active': True}, fee_changes={r['event_ticker']: []},
             series_fee_changes=[], synthetic=True), now - 302)
        for i in range(-301, 1):
            reference(now + i)
        assert not e.executor.orders
        quote('.88', '.90', now + .01)
        order = e.executor.orders[ticker]
        assert order.side == side and order.quantity > 0
        assert not store.list(kind='opportunity')
        assert e.latest[ticker]['probability']['paths'] == 4000
        assert e.latest[ticker]['conservative_probability'] >= c.min_probability
        send('trade', dict(market_ticker=ticker, trade_id='causal-opposing-volume',
             taker_outcome_side='no' if side == 'yes' else 'yes',
             yes_price_dollars=str(D(order.limit) if side == 'yes' else 1-D(order.limit)),
             count_fp='0.40', ts_ms=(now + .50) * 1000), now + .50)
        assert e.executor.positions[ticker].quantity == .40
        assert len(store.list(kind='opportunity')) == 1
        buys = [x['body'] for x in store.list(kind='fill') if x['body']['action'] == 'buy']
        assert len(buys) == 1
        if ending.startswith('settle'):
            result = side if ending == 'settle_win' else ('no' if side == 'yes' else 'yes')
            final = {**r, 'status': 'finalized', 'result': result}
            close = e.markets[ticker].close_time
            proof = dict(source='kalshi_rest', market=final, series=series)
            send('settlement', dict(market_ticker=ticker, result=result, evidence=proof), close + 3)
            send('settlement', dict(market_ticker=ticker, result=result, evidence=proof), close + 4)
        else:
            bid, ask = ('.995', '.999') if ending == 'take_profit' else ('.50', '.52')
            for i in (1, 2):
                reference(now + i)
                quote(bid, ask, now + i + .01)
        fills = [x['body'] for x in store.list(kind='fill')]
        sells = [x for x in fills if x['action'] == 'sell']
        results = store.list(kind='trade_result')
        assert len(results) == 1
        out = results[0]['body']
        assert out['net_pnl'] > 0 if ending in ('take_profit', 'settle_win') else out['net_pnl'] < 0
        spent = sum((D(x['price']) * D(x['quantity']) for x in buys), D(0))
        earned = sum((D(x['price']) * D(x['quantity']) for x in sells), D(0))
        earned += D('.40') if ending == 'settle_win' else D(0)
        fees = sum((D(x['fee']) for x in fills), D(0))
        assert out['net_pnl'] == pytest.approx(float(earned - spent - fees))
        assert all(D(x['quantity']) > 0 and D(x['quantity']) % D('.01') == 0 for x in fills)
        assert not e.executor.positions and not e.executor.risk.reserved
        assert store.state(e.run_id, ticker) == 'CLOSED'
        checkpoint = store.load_checkpoint(e.run_id)
        assert not checkpoint['positions'] and not checkpoint['risk']['reserved']
        store.engine.dispose()
        with TestClient(create_app(store, settings=Settings(data_dir=str(tmp_path)), config=c,
                                   collect_live=False)) as client:
            params = dict(mode=mode, run_id=e.run_id)
            trades = client.get('/api/trades', params=params)
            assert trades.status_code == 200, trades.text
            assert trades.json()['total'] == 1
            m = client.get('/api/analytics', params=params)
            assert m.status_code == 200, m.text
            assert m.json()['net_pnl'] == pytest.approx(out['net_pnl'])
            assert client.get('/api/replay/' + order.opportunity_id).status_code == 200
        print('LIFECYCLE_RESULT ' + json.dumps(dict(mode=mode, side=side, preset=preset,
              ending=ending, bought=float(sum(D(x['quantity']) for x in buys)),
              sold=float(sum(D(x['quantity']) for x in sells)), net_pnl=out['net_pnl'],
              fees=float(fees), complete=True)))
    finally:
        recorder.close()
    recovered = list(read_events(recorder.path))
    assert len(recovered) == len(rows)
    assert [x['id'] for x in recovered] == [x['id'] for x in rows]


@pytest.mark.parametrize('operator', ['>=', '>', '<=', '<'])
@pytest.mark.parametrize('reference', [77900.0, 78100.0])
def test_probability_comparator_and_causality(market, now, operator, reference):
    spec = replace(market.spec, strike=78000.0, comparison_operator=operator)
    c = Strategy()
    past = [Tick(now, now, reference)]
    future = [Tick(now+1, now+1, reference * 2)]
    a = probability(spec, past, now, 0, c)
    b = probability(spec, past+future, now, 0, c)
    assert a == b
    assert a['p_yes'] == float(spec.yes(reference))
    assert a['p_no'] == 1-a['p_yes']
    assert a['conservative_yes'] + a['conservative_no'] <= 1
    assert a['simulation_uncertainty'] > 0


@pytest.mark.parametrize('mode', ['PAPER', 'BACKTEST'])
@pytest.mark.parametrize('side', ['yes', 'no'])
def test_lifecycle_deactivation_does_not_allow_reentry(scenario, store, market, now, mode, side):
    s = scenario(mode, side, buy=False)
    s.e.connection = 'VERIFY'
    def ingest(kind, msg, when, ident):
        s.clock[0] = when
        row = dict(id=ident, received=when, monotonic_ns=int((when-now+100)*1e9),
                   connection_id='VERIFY', payload=dict(type=kind, msg=msg))
        assert s.e.ingest(row)
    ingest('market_lifecycle_v2', dict(market_ticker=market.ticker, event_type='deactivated'), now, 'deactivated')
    assert not s.e.executor.orders
    b = make_book(side, '.88', '.90', now+.1)
    ingest('orderbook_snapshot', dict(market_ticker=market.ticker, ts_ms=(now+.1)*1000,
           yes_dollars_fp=[[str(p),str(q)] for p,q in b.yes.items()],
           no_dollars_fp=[[str(1-p),str(q)] for p,q in b.no.items()]), now+.1, 'new-quote')
    orders = [r['body'] for r in store.list(kind='order') if r['body'].get('status') == 'submitted']
    print('DEACTIVATION_OBSERVED', mode, side, json.dumps(orders))
    assert not orders, 'A fresh quote must not independently undo venue deactivation'


@pytest.mark.parametrize('price', [.891, .902, .912, .924, .929])
def test_fixed_contracts_returns_five_when_no_other_budget_binds(price):
    c = Strategy()
    r = Risk(c)
    q = r.size(price, 0)
    print('SIZING_OBSERVED', price, q)
    assert q == 5, 'All budgets permit five; a float floor must not shrink the configured fixed size'


@pytest.mark.parametrize('side', ['yes', 'no'])
@pytest.mark.parametrize('ask', ['.938', '.939'])
def test_passive_price_obeys_decimal_discount_on_supported_grid(market, now, side, ask):
    c = Strategy()
    b = make_book(side, '.91', ask, now)
    conservative = .979
    p = passive_price(market, b, side, conservative, c)
    discount = min(D(c.passive_discount), (D(ask)-D('.91'))/2,
                   max(D(0), D(conservative)-D(ask)-D(c.min_edge)))
    expected = market.snap(D(ask)-discount)
    print('PASSIVE_PRICE_OBSERVED', side, ask, expected, p)
    assert D(p) == D(expected)


@pytest.mark.parametrize('mode', ['PAPER', 'BACKTEST'])
@pytest.mark.parametrize('side', ['yes', 'no'])
def test_sell_price_does_not_lose_an_extra_tick(store, market, now, mode, side):
    c = Strategy()
    e = initialize(store, market, now, c, mode)
    b = make_book(side, '.88', '.90', now)
    d = dict(decision='TRADE_CANDIDATE', side=side, conservative_probability=.979)
    order = e.submit(market, b, d, 'op', now, True)
    assert order is not None
    e.fill(order, .40, order.limit, now+.5, True)
    b = make_book(side, '.939', '.949', now+1)
    p = {'conservative_'+side: .1}
    e.monitor(market,b,p,now+1,'intent')
    b.received = b.source_time = now+2
    e.monitor(market,b,p,now+2,'sell')
    fills = [r['body'] for r in store.list(kind='fill') if r['body']['action']=='sell']
    assert len(fills)==1
    expected = market.snap(D('.939')-D(c.slippage))
    print('EXIT_PRICE_OBSERVED', mode, side, expected, fills[0]['price'])
    assert D(fills[0]['price']) == D(expected)
