"""Public UI interactions with synthetic APIs; never connects to a trading service."""
import os
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
preview = runpy.run_path(str(ROOT / 'scripts/preview_public_ui.py'))


def test_public_ui_selection_history_owner_and_layout():
    playwright = pytest.importorskip('playwright.sync_api')
    snapshot = preview['sample_view']()
    state = {'reject': False, 'stale': False, 'expires': 60, 'view_fail': False}
    posts = []
    errors = []

    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.on('pageerror', lambda error: errors.append(str(error)))

        def route(request_route):
            request = request_route.request
            path = request.url.split('http://ui.test')[-1]
            if path == '/api/view':
                import time
                snapshot['updated_at'] = time.time()
                snapshot['stale'] = state['stale']
                return request_route.fulfill(status=503 if state['view_fail'] else 200, json=snapshot)
            if path == '/api/stop' and request.method == 'GET':
                return request_route.fulfill(json=dict(enabled=True, status='idle', assets={}))
            if path == '/api/unlock':
                return request_route.fulfill(json=dict(token='fixture-token', expires_in=state['expires']))
            if path == '/api/control':
                payload = request.post_data_json
                posts.append(payload)
                if state['reject']:
                    return request_route.fulfill(status=409, json={'detail': 'Settings changed. Refresh and review.'})
                policy = snapshot['assets'][next(i for i,a in enumerate(snapshot['assets']) if a['asset']==payload['asset'])]['live_policy']
                policy.update(enabled=payload['enabled'], contracts=payload['contracts'], revision=policy['revision']+1)
                return request_route.fulfill(json=policy)
            if path.startswith('/api/history/'):
                from urllib.parse import parse_qs, urlparse
                url = urlparse(path)
                asset = url.path.rsplit('/', 1)[-1]
                offset = int(parse_qs(url.query)['offset'][0])
                rows = preview['sample_trades'](asset)
                return request_route.fulfill(json=dict(rows=rows[offset:offset+25], total=len(rows), stale=False))
            filename = 'index.html' if path == '/' else path.lstrip('/')
            if filename not in ('index.html', 'viewer.js', 'viewer.css', 'logo.svg'):
                return request_route.fulfill(status=404)
            content = (ROOT / 'src/btc15/public_static' / filename).read_text()
            if filename == 'index.html':
                content = content.replace('All-time recorded performance, market status, and trade history.',
                                          'LOCAL PREVIEW · Synthetic data · Recorded performance and market status.')
            mime = {'index.html': 'text/html', 'viewer.js': 'text/javascript', 'viewer.css': 'text/css', 'logo.svg': 'image/svg+xml'}[filename]
            return request_route.fulfill(body=content, content_type=mime)

        page.route('**/*', route)
        page.goto('http://ui.test/')
        expect = playwright.expect
        expect(page.locator('#connection')).to_have_text('Connected')
        expect(page.locator('#live-contracts')).to_have_value('10')
        expect(page.locator('#live-contracts')).to_be_disabled()
        assert page.locator('#comparison .market-button').count() == 7
        metrics = page.evaluate('summarize(ownerLatest.assets)')
        assert metrics['completed_trades'] == 1090
        assert metrics['breakeven_trades'] == 7
        assert metrics['win_rate'] == pytest.approx(958/1090)
        assert page.evaluate('summarize(ownerLatest.assets.slice(1)).realized_pnl') is None
        assert page.evaluate('summarize(ownerLatest.assets.map(a=>({...a,completed_trades:0,wins:0}))).win_rate') is None
        assert page.evaluate('leaders(ownerLatest.assets.map(a=>({...a,realized_pnl:1}))).length') == 4
        assert page.evaluate('leaders(ownerLatest.assets.map(a=>({...a,completed_trades:0}))).length') == 0
        assert page.evaluate('leaders(ownerLatest.assets.map(a=>({...a,realized_pnl:null}))).length') == 0
        page.locator('#comparison .market-button').filter(has_text='ETH').click()
        expect(page.locator('#detail-title')).to_have_text('ETH / Market detail')
        expect(page.locator('#market-tabs [data-asset=ETH]')).to_have_attribute('aria-pressed', 'true')
        expect(page.locator('#trades-title')).to_have_text('ETH · Latest 5 trades')
        assert posts == []
        page.locator('#browse').click()
        expect(page.locator('#trades tbody tr')).to_have_count(25)
        page.locator('#next').click()
        expect(page.locator('#page-info')).to_have_text('26–38 of 38')
        expect(page.locator('#trades tbody tr')).to_have_count(13)
        expect(page.locator('#next')).to_be_disabled()
        page.locator('#market-tabs [data-asset=WTI]').click()
        expect(page.locator('#page-info')).to_have_text('1–25 of 38')
        expect(page.locator('#trades tbody tr').first).to_contain_text('DEMO-WTI')
        page.locator('#browse').click()
        page.locator('#market-tabs [data-asset=BTC]').click()
        page.locator('#owner-passcode').fill('test-only-passcode-1234')
        page.locator('#unlock-form button').click()
        expect(page.locator('#owner-passcode')).to_have_value('')
        expect(page.locator('#live-contracts')).to_be_enabled()
        page.locator('#live-contracts').fill('13')
        page.wait_for_timeout(5200)
        expect(page.locator('#live-contracts')).to_have_value('13')
        page.locator('#live-confirm').check()
        page.locator('#live-form button[type=submit]').click()
        expect(page.locator('#live-message')).to_contain_text('Saved: 13')
        assert posts[-1]['asset'] == 'BTC' and posts[-1]['contracts'] == 13
        expect(page.locator('#live-current')).to_contain_text('13 contracts')
        page.locator('#live-confirm').check()
        page.locator('#disable-buying').click()
        expect(page.locator('#live-message')).to_contain_text('New buys disabled')
        assert posts[-1]['enabled'] is False
        state['reject'] = True
        page.locator('#live-contracts').fill('14')
        page.locator('#live-confirm').check()
        page.locator('#live-form button[type=submit]').click()
        expect(page.locator('#live-message')).to_contain_text('Settings changed')
        expect(page.locator('#live-contracts')).to_be_disabled()
        state['view_fail'] = True
        page.wait_for_timeout(5200)
        expect(page.locator('#connection')).to_contain_text('Updates unavailable')
        expect(page.locator('#asset-stop-fields button')).to_be_disabled()
        expect(page.locator('#market-status .live-buying').first).to_have_text('Unavailable')
        state['view_fail'] = False
        state['expires'] = 1
        page.locator('#owner-passcode').fill('test-only-passcode-1234')
        page.locator('#unlock-form button').click()
        page.wait_for_timeout(1300)
        expect(page.locator('#unlock-status')).to_have_text('Locked')
        assert page.evaluate('localStorage.length') == 0
        assert page.evaluate('sessionStorage.length') == 0
        page.reload()
        expect(page.locator('#comparison .market-button')).to_have_count(7)
        for width, height, name in [(1440, 1000, 'desktop'), (768, 1024, 'tablet'), (390, 844, 'mobile')]:
            page.set_viewport_size({'width': width, 'height': height})
            assert page.evaluate('document.documentElement.scrollWidth') <= width
            if os.environ.get('PROJECT15_UI_SCREENSHOTS') and name != 'tablet':
                folder = ROOT / 'reports/public-ui'
                folder.mkdir(exist_ok=True)
                page.screenshot(path=str(folder / f'{name}.png'), full_page=True)
        assert not errors
        browser.close()
