import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from btc15 import public_dashboard as public


def fleet_data():
    return dict(
        live_only=True, realized_pnl=1.25, totals_complete=True,
        live={"secret": "private-controls"},
        assets=[dict(
            asset=asset, run_id=asset + '-signals', healthy=True,
            price=100, realized_pnl=1.25, wins=2, losses=1,
            win_rate=2/3, current_streak=-1, longest_win_streak=2, longest_loss_streak=1,
            operational=dict(state='COLLECTING', failure='private-file-path'),
            markets=[dict(ticker=asset + '-market', fresh=True, manual_purchases={'secret': 1})],
        ) for asset in ('BTC', 'ETH', 'SOL', 'XRP')],
    )


@pytest.mark.parametrize('market_result', ['yes', 'no', None])
def test_snapshot_reads_fixed_paths_and_removes_private_fields(market_result):
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path == '/api/fleet':
            return httpx.Response(200, json=fleet_data())
        return httpx.Response(200, json=dict(stale=True, rows=[dict(
            market='<script>alert(1)</script>', timestamp=100, run_id='private-run',
            body=dict(status='CLOSED', side='yes', bought=10, entry=.95, exit=.99,
                      fees=.1, net_pnl=.3, market_result=market_result,
                      probability={'secret': 1}, order_id='secret'),
        )]))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://127.0.0.1:8000') as client:
            return await public.read_snapshot(client)

    snapshot = asyncio.run(run())
    assert len(snapshot['assets']) == 4
    assert snapshot['live_only'] is True
    assert snapshot['assets'][0]['trades'][0]['net_pnl'] == .3
    assert snapshot['assets'][0]['trades'][0]['market_result'] == market_result
    assert snapshot['assets'][0]['trades_stale'] is True
    assert snapshot['assets'][0]['win_rate'] == pytest.approx(2/3)
    assert snapshot['assets'][0]['current_streak'] == -1
    assert snapshot['assets'][0]['longest_win_streak'] == 2
    assert snapshot['assets'][0]['longest_loss_streak'] == 1
    assert 'secret' not in str(snapshot)
    assert 'private-' not in str(snapshot)
    assert len(calls) == 5
    assert all(request.method == 'GET' for request in calls)
    assert {r.url.path for r in calls} == {'/api/fleet', *[f'/assets/{a}/api/trades' for a in ('BTC', 'ETH', 'SOL', 'XRP')]}
    assert all(r.url.params['limit'] == '5' for r in calls[1:])


def test_public_requests_cannot_reach_controls_or_choose_upstream(monkeypatch):
    calls = []

    async def read(client):
        calls.append(client.base_url)
        return dict(assets=[], updated_at=public.time.time())

    monkeypatch.setattr(public, 'read_snapshot', read)
    with TestClient(public.create_public_app()) as client:
        baseline = len(calls)
        for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'):
            for path in ('/api/view', '/api/shutdown', '/api/strategy', '/api/live/assets/BTC',
                         '/api/manual/orders', '/assets/BTC/api/strategy', '/'):
                response = client.request(method, path, json={'confirm': True}, headers={
                    'Origin': 'http://127.0.0.1:8000', 'X-HTTP-Method-Override': 'GET',
                })
                assert response.status_code == 405
        for path in ('/api/strategy', '/api/shutdown', '/api/live/status', '/api/manual/orders',
                     '/assets/BTC/', '/assets/BTC/api/trades', '/openapi.json', '/docs',
                     '/static/app.js', '/viewer.js/../../.env'):
            assert client.get(path).status_code == 404
        response = client.get('/api/view?url=http://evil.test/api/shutdown', headers={'Authorization': 'secret'})
        assert response.status_code == 200
        assert not response.json()['stale']
        assert response.headers['cache-control'] == 'no-store'
        assert "frame-ancestors 'none'" in response.headers['content-security-policy']
        assert len(calls) == baseline
        assert client.get('/').status_code == 200
        script = client.get('/viewer.js').text
        assert 'innerHTML' not in script
        assert 'textContent' in script
        assert client.get('/viewer.css').status_code == 200


def test_no_upstream_data_is_generic_503(monkeypatch):
    async def read(client):
        raise httpx.ConnectError('private-key-path-do-not-expose')

    monkeypatch.setattr(public, 'read_snapshot', read)
    with TestClient(public.create_public_app()) as client:
        response = client.get('/api/view')
        assert response.status_code == 503
        assert response.json()['stale'] is True
        assert 'private-key' not in response.text


def test_old_snapshot_is_marked_stale(monkeypatch):
    async def read(client):
        return dict(assets=[], updated_at=public.time.time() - 25)

    monkeypatch.setattr(public, 'read_snapshot', read)
    with TestClient(public.create_public_app()) as client:
        assert client.get('/api/view').json()['stale'] is True


def test_public_cli_does_not_load_trading_settings(monkeypatch):
    from btc15 import cli

    monkeypatch.setattr('sys.argv', ['btc15', 'public-dashboard'])
    monkeypatch.setattr(cli.Settings, 'env', lambda: pytest.fail('Must not load credentials'))
    served = []
    monkeypatch.setattr(cli, 'serve_dashboard', lambda app, port: served.append(port))
    cli.main()
    assert served == [8001]


@pytest.fixture
def owner_app(monkeypatch, tmp_path):
    import hashlib
    import json

    code = 'test-only-passcode-1234'
    salt = bytes(range(16))
    path = tmp_path / 'secret.json'
    path.write_text(json.dumps(dict(salt=salt.hex(), digest=hashlib.pbkdf2_hmac(
        'sha256', code.encode(), salt, 600_000).hex())))
    monkeypatch.setenv('PROJECT15_SHUTDOWN_SECRET_FILE', str(path))
    requests = []
    replies = {'post': 202, 'status': 'stopped', 'control': 200}
    clock = [1000.0]
    monkeypatch.setattr(public, 'monotonic', lambda: clock[0])

    def handler(request):
        requests.append(request)
        if request.url.path == '/api/live/control':
            body = json.loads(request.content)
            return httpx.Response(replies['control'], json={**body, 'revision': body['revision'] + 1})
        if request.method == 'POST':
            return httpx.Response(replies['post'], json={'status': 'stopping', 'detail': 'private-secret'})
        return httpx.Response(200, json={'status': replies['status']})

    original = httpx.AsyncClient
    monkeypatch.setattr(public.httpx, 'AsyncClient', lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(handler)))
    snapshot = dict(assets=[dict(asset=a, markets=[dict(ticker=a+'-market', fresh=True)], live_policy=dict(enabled=False, contracts=10, revision=1)) for a in ('BTC','ETH','SOL','XRP')], live_available=True, updated_at=public.time.time())

    async def read(client):
        return snapshot

    monkeypatch.setattr(public, 'read_snapshot', read)
    return public.create_public_app(), code, requests, replies, clock, snapshot


def unlock(client, code):
    response = client.post('/api/unlock', headers={'Origin': 'http://testserver'}, json={'passcode': code})
    assert response.status_code == 200
    assert response.json()['expires_in'] == 60
    return {'Origin': 'http://testserver', 'Authorization': 'Bearer ' + response.json()['token']}


def live_payload(asset='BTC'):
    return dict(asset=asset, ticker=asset+'-market', enabled=True, contracts=12, revision=1, confirm='ENABLE_REAL_TRADING')


def test_unlock_required_for_every_action(owner_app):
    app, code, requests, _, _, _ = owner_app
    with TestClient(app) as client:
        for route, body in [('/api/control', live_payload()), ('/api/stop', {'confirm': True})]:
            assert client.post(route, headers={'Origin': 'http://testserver'}, json={**body, 'passcode': code}).status_code == 401
        assert not requests
        headers = unlock(client, code)
        response = client.post('/api/control', headers=headers, json=live_payload())
        assert response.status_code == 200
        assert response.json() == dict(enabled=True, contracts=12, revision=2)
        assert requests[-1].url.path == '/api/live/control'
        assert requests[-1].headers['origin'] == 'http://127.0.0.1:8000'
        assert 'authorization' not in requests[-1].headers
        assert code not in str(requests[-1].content)
        assert 'token' not in client.get('/api/view').text
        assert 'token' not in client.get('/api/stop').text


def test_fixed_sixty_second_expiry_for_live_and_shutdown(owner_app):
    app, code, requests, _, clock, _ = owner_app
    with TestClient(app) as client:
        headers = unlock(client, code)
        clock[0] += 59.9
        assert client.post('/api/control', headers=headers, json=live_payload()).status_code == 200
        before = len(requests)
        clock[0] = 1060.0
        for route, body in [('/api/control', live_payload()), ('/api/stop', {'confirm': True})]:
            assert client.post(route, headers=headers, json=body).status_code == 401
        assert len(requests) == before
        renewed = unlock(client, code)
        assert renewed != headers
        assert client.post('/api/control', headers=renewed, json=live_payload('ETH')).status_code == 200
        # Reauthentication doesn't revive the old token.
        assert client.post('/api/stop', headers=headers, json={'confirm': True}).status_code == 401


def test_invalid_live_settings_and_stale_data_are_blocked(owner_app):
    app, code, requests, replies, _, snapshot = owner_app
    with TestClient(app) as client:
        headers = unlock(client, code)
        for change in ({'contracts': 0}, {'contracts': 21}, {'contracts': 1.5}, {'contracts': True}, {'enabled': 'yes'}, {'confirm': ''}, {'revision': -1}, {'extra': 'field'}):
            assert client.post('/api/control', headers=headers, json={**live_payload(), **change}).status_code == 422
        assert client.post('/api/control', headers=headers, json=live_payload('DOGE')).status_code == 409
        assert not requests
        snapshot['updated_at'] -= 30
        assert client.post('/api/control', headers=headers, json=live_payload()).status_code == 409
        snapshot['updated_at'] = public.time.time()
        replies['control'] = 409
        assert client.post('/api/control', headers=headers, json=live_payload()).status_code == 409


def test_live_disable_and_all_four_assets(owner_app):
    app, code, requests, _, _, _ = owner_app
    with TestClient(app) as client:
        headers = unlock(client, code)
        for asset in ('BTC', 'ETH', 'SOL', 'XRP'):
            response = client.post('/api/control', headers=headers, json={**live_payload(asset), 'enabled': False, 'contracts': 1, 'confirm': ''})
            assert response.status_code == 200
            assert response.json()['enabled'] is False
        assert len(requests) == 4


def test_wrong_passcodes_rate_limited_even_with_spoofed_ip(owner_app):
    app, code, requests, _, _, _ = owner_app
    with TestClient(app) as client:
        for i in range(5):
            response = client.post('/api/unlock', headers={'Origin': 'http://testserver', 'X-Forwarded-For': f'192.0.2.{i}'}, json={'passcode': 'incorrect-code-123'})
            assert response.status_code == 403
        response = client.post('/api/unlock', headers={'Origin': 'http://testserver'}, json={'passcode': code})
        assert response.status_code == 429
        assert not requests


def test_shutdown_guard_confirmation_and_duplicate_requests(owner_app):
    app, code, requests, replies, _, _ = owner_app
    with TestClient(app) as client:
        headers = unlock(client, code)
        assert client.post('/api/stop', headers=headers, json={'confirm': False}).status_code == 422
        replies['post'] = 409
        response = client.post('/api/stop', headers=headers, json={'confirm': True})
        assert response.status_code == 409
        assert 'private-secret' not in response.text
        replies['post'] = 202
        assert client.post('/api/stop', headers=headers, json={'confirm': True}).status_code == 202
        before = len([r for r in requests if r.method == 'POST'])
        assert client.post('/api/stop', headers=headers, json={'confirm': True}).status_code == 202
        assert len([r for r in requests if r.method == 'POST']) == before
        assert client.post('/api/control', headers=headers, json=live_payload()).status_code == 409


def test_shutdown_uncertain_response_is_not_success(owner_app):
    app, code, requests, replies, _, _ = owner_app
    replies.update(post=503, status='idle')
    with TestClient(app) as client:
        headers = unlock(client, code)
        assert client.post('/api/stop', headers=headers, json={'confirm': True}).status_code == 502
        assert client.get('/api/stop').json()['status'] == 'unknown'


def test_origin_body_limit_and_no_passcode_configuration(owner_app, monkeypatch):
    app, code, requests, _, _, _ = owner_app
    with TestClient(app) as client:
        assert client.post('/api/unlock', json={'passcode': code}).status_code == 403
        assert client.post('/api/unlock', headers={'Origin': 'http://testserver'}, json={'passcode': 'a'*3000}).status_code == 413
        headers = unlock(client, code)
        headers['Origin'] = 'https://evil.example'
        assert client.post('/api/control', headers=headers, json=live_payload()).status_code == 403
        assert client.post('/api/stop', headers=headers, json={'confirm': True}).status_code == 403
        assert not requests
    monkeypatch.delenv('PROJECT15_SHUTDOWN_SECRET_FILE')
    with TestClient(public.create_public_app()) as client:
        assert not client.get('/api/stop').json()['enabled']
        assert client.post('/api/unlock', json={'passcode': code}).status_code == 503
