from dataclasses import replace

import pytest
from test_strategy_reverification import initialize, make_book

from btc15.config import Strategy
from btc15.domain import Book
from btc15.strategies.settlement_edge.model import Tick
from btc15.strategies.settlement_edge.rules import evaluate


@pytest.mark.parametrize('asset', ['BTC','ETH','SOL','XRP','BNB','HYPE','DOGE','GOLD','SILVER','WTI'])
@pytest.mark.parametrize('side', ['yes','no'])
@pytest.mark.parametrize('bid,ask', [('.90','.90'),('.70','.95')])
def test_zero_and_wide_spreads_pass_signal_and_submission(store, market, asset, side, bid, ask):
    suffix = 'active' if asset == 'BTC' else asset.lower()
    c = replace(Strategy.load(f'config/settlement-edge-{suffix}-paper.json'), max_spread=.001)
    now = market.close_time - 300
    book = make_book(side, bid, ask, now)
    book.validate()
    p = {'p_' + side: .99, 'lead': dict(side=side, confirmed_normal=True, confirmation_samples=1)}
    d = evaluate(market, book, Tick(now, now, market.spec.strike + (10 if side=='yes' else -10)),
                 dict(regime='NORMAL'), p, dict(score=100,reasons=[]), now, c)
    assert not d['spread_filter_enabled']
    assert d['decision'] == 'TRADE_CANDIDATE', d['reasons']
    ex = initialize(store,market,now,c,'PAPER')
    assert ex.submit(market,book,d,'spread-disabled',now,True) is not None


def test_locked_snapshot_and_delta_are_valid_but_crossing_is_not():
    book = Book()
    book.snapshot(dict(yes_dollars_fp=[['.9','2']], no_dollars_fp=[['.9','2']]), 100)
    assert book.valid
    book.delta(dict(side='yes',price_dollars='.9',delta_fp='1'),101)
    assert book.valid
    with pytest.raises(ValueError,match='Crossed'):
        book.delta(dict(side='yes',price_dollars='.91',delta_fp='1'),102)
    assert not book.valid
