import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from btc15.public_performance import build_performance, trade_day
from btc15.public_site import create_app


def ts(value):
    return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo('America/New_York')).timestamp()


def row(opened, closed, pnl, status='CLOSED'):
    return dict(market='TEST', opened=ts(opened), exit_timestamp=ts(closed) if closed else None,
                status=status, net_pnl=pnl)


def test_daily_closure_attribution_zero_days_and_all_time():
    now = ts('2026-09-30T12:00:00')
    rows = [row('2026-09-27T23:50:00', '2026-09-28T00:01:00', 8),
            row('2026-09-28T10:00:00', '2026-09-28T10:10:00', -3),
            row('2026-09-28T11:00:00', '2026-09-28T11:10:00', 0),
            row('2026-09-30T11:00:00', None, 99, 'OPEN')]
    result = build_performance({'BTC': dict(rows=rows, updated_at=now)}, ('BTC',), now)
    daily = {d['date']: d['assets']['BTC'] for d in result['days']}
    assert daily['2026-09-27']['realized_pnl'] == 0
    assert daily['2026-09-27']['opened_trades'] == 1
    assert daily['2026-09-28']['realized_pnl'] == 5
    assert daily['2026-09-28']['completed_trades'] == 3
    assert daily['2026-09-28']['win_rate'] == 1 / 3
    assert daily['2026-09-29']['realized_pnl'] == 0
    assert result['all_time']['BTC']['realized_pnl'] == 5
    assert result['all_time']['BTC']['open_positions'] == 1
    assert result['stale'] is False


def test_missing_stale_and_undated_results_are_not_silently_zero():
    now = ts('2026-09-30T12:00:00')
    rows = [row('2026-09-28T10:00:00', None, 4),
            row('2026-09-28T10:00:00', '2026-09-28T11:00:00', None)]
    result = build_performance({'BTC': dict(rows=rows, updated_at=now - 200)}, ('BTC', 'ETH'), now)
    assert result['missing_assets'] == ['ETH']
    assert result['stale']
    assert result['excluded_undated_trades'] == 1
    assert result['all_time']['BTC']['realized_pnl'] is None
    assert result['days'][0]['assets']['BTC']['realized_pnl'] is None
    assert trade_day(float('nan')) is None
    assert trade_day(None) is None


def test_history_day_filter_and_dst(tmp_path):
    # This New York day is 25 hours long. Include both instances of 01:30.
    start = ts('2026-11-01T00:00:00')
    end = ts('2026-11-02T00:00:00')
    rows = [dict(market=str(i), status='CLOSED', opened=start - 60, exit_timestamp=t)
            for i, t in enumerate([start - 1, start, start + 5400, start + 9000, end - 1, end])]
    import time
    (tmp_path / 'history-BTC.json').write_text(json.dumps(dict(rows=rows, total=len(rows), updated_at=time.time())))
    with TestClient(create_app(tmp_path)) as client:
        result = client.get('/api/history/BTC?day=2026-11-01&limit=2').json()
        assert result['total'] == 4
        assert [r['market'] for r in result['rows']] == ['1', '2']
        assert client.get('/api/history/BTC?day=2026-11-01&offset=2').json()['rows'] == rows[3:5]
        assert client.get('/api/history/BTC?day=2026-11-01&basis=opened').json()['total'] == 0
        assert client.get('/api/history/BTC?day=2026-10-31&basis=opened').json()['total'] == 6
        for query in ['day=bad', 'day=2026-02-30', 'basis=invalid', 'day=9999-12-31']:
            assert client.get('/api/history/BTC?' + query).status_code == 422
        assert client.get('/performance').status_code == 200
        assert client.get('/performance.js').status_code == 200
        assert client.get('/api/performance').status_code == 503
        (tmp_path / 'performance.json').write_text(json.dumps({'updated_at': time.time(), 'days': []}))
        assert client.get('/api/performance').json()['stale'] is False
        assert client.post('/api/performance').status_code == 405
