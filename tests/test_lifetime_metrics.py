import pytest

from btc15.analytics import lifetime_performance


def record(i, body, run="a", opportunity="op"):
    return dict(id=str(i), timestamp=i, body=body, run_id=run, market="BTC", opportunity_id=opportunity)


def test_realized_metrics_order_streaks_and_breakeven():
    # Interleaved runs must be ordered by trade completion, not grouped by run.
    pnls = [10, 5, -4, -8, 0, -2, 6, 3]
    rows = [record(i, dict(net_pnl=p), run=str(i % 2)) for i, p in enumerate(pnls)]
    data = lifetime_performance(list(reversed(rows)), [])
    assert data["net_pnl"] == 10
    assert data["max_drawdown"] == 14
    assert data["average_pnl"] == 1.25
    assert data["wins"] == 4 and data["losses"] == 3 and data["breakeven_trades"] == 1
    assert data["win_rate"] == 0.5
    assert data["profit_factor"] == pytest.approx(24 / 14)
    assert data["current_streak"] == 2
    assert data["longest_win_streak"] == 2 and data["longest_loss_streak"] == 2
    assert lifetime_performance(rows[:5], [])["current_streak"] == 0
    assert lifetime_performance(rows[:4], [])["current_streak"] == -2


def test_empty_and_one_sided_results():
    data = lifetime_performance([], [])
    assert data["win_rate"] is None and data["average_pnl"] is None and data["profit_factor"] is None
    assert data["open_exposure"] == data["current_streak"] == data["max_drawdown"] == 0
    assert lifetime_performance([record(1, dict(net_pnl=2))], [])["profit_factor"] is None
    losses = lifetime_performance([record(1, dict(net_pnl=-2))], [])
    assert losses["profit_factor"] == 0 and losses["max_drawdown"] == 2


def test_exposure_partial_exits_and_closed_trades():
    fills = [
        record(1, dict(action="buy", quantity=10, price=0.6, fee=0.2)),
        record(2, dict(action="sell", quantity=4, price=0.9, fee=0.1)),
        record(3, dict(action="buy", quantity=2, price=0.5, fee=0.1), run="b"),
        record(4, dict(action="buy", quantity=1, price=0.8, fee=0), opportunity="settled"),
    ]
    closed = [record(5, dict(net_pnl=0.2), opportunity="settled")]
    data = lifetime_performance(closed, list(reversed(fills)))
    assert data["open_exposure"] == pytest.approx(6.2 * 0.6 + 1.1)
    assert data["open_trades"] == 2
    assert data["net_pnl"] == 0.2
    # The same opportunity ID in another run is an independent position.
    closed.append(record(6, dict(net_pnl=0.1), run="b"))
    data = lifetime_performance(closed, fills)
    assert data["open_exposure"] == pytest.approx(3.72)
    assert data["open_trades"] == 1
