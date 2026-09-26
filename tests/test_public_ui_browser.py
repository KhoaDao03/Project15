"""Public UI interactions with synthetic APIs; never connects to a trading service."""
import os
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
preview = runpy.run_path(str(ROOT / 'scripts/preview_public_ui.py'))


def test_public_ui_selection_history_and_layout():
    playwright = pytest.importorskip('playwright.sync_api')
    snapshot = preview['sample_view']()
    import time
    for asset in snapshot['assets']:
        for market in asset['markets']:
            yes = .35 if asset['asset'] == 'ETH' else .82
            market['probability'] = dict(available=True, p_yes=yes, p_no=1-yes,
                                         side='no' if asset['asset']=='ETH' else 'yes', confidence=.60 if asset['asset']=='ETH' else .75,
                                         timestamp=time.time(), quality_warning=False)
    state = {'stale': False, 'view_fail': False}
    requests = []
    errors = []

    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.on('pageerror', lambda error: errors.append(str(error)))

        def route(request_route):
            request = request_route.request
            path = request.url.split('http://ui.test')[-1]
            requests.append((request.method, path))
            assert request.method == 'GET'
            if path == '/api/view':
                import time
                snapshot['updated_at'] = time.time()
                snapshot['stale'] = state['stale']
                return request_route.fulfill(status=503 if state['view_fail'] else 200, json=snapshot)
            if path.startswith('/api/history/'):
                from urllib.parse import parse_qs, urlparse
                url = urlparse(path)
                asset = url.path.rsplit('/', 1)[-1]
                offset = int(parse_qs(url.query)['offset'][0])
                rows = preview['sample_trades'](asset)
                return request_route.fulfill(json=dict(rows=rows[offset:offset+25], total=len(rows), stale=False))
            filename = 'index.html' if path == '/' else path.lstrip('/')
            if filename not in ('index.html', 'viewer.js', 'viewer.css', 'logo.svg', 'section-logo.svg'):
                return request_route.fulfill(status=404)
            content = (ROOT / 'src/btc15/public_static' / filename).read_text()
            if filename == 'index.html':
                content = content.replace('All-time recorded performance, market status, and trade history.',
                                          'LOCAL PREVIEW · Synthetic data · Recorded performance and market status.')
            mime = {'index.html': 'text/html', 'viewer.js': 'text/javascript', 'viewer.css': 'text/css', 'logo.svg': 'image/svg+xml', 'section-logo.svg': 'image/svg+xml'}[filename]
            return request_route.fulfill(body=content, content_type=mime)

        page.route('**/*', route)
        page.goto('http://ui.test/')
        expect = playwright.expect
        expect(page.locator('#connection')).to_have_text('Connected')
        expect(page.locator('form, #owner-section, .shutdown')).to_have_count(0)
        expect(page.locator('body')).not_to_contain_text('Owner controls')
        assert page.locator('#comparison .market-button').count() == 10
        expect(page.locator('[aria-label="Market status"] thead')).to_contain_text('Entry confidence')
        expect(page.locator('#market-status .probability').first).to_have_text('YES 75.0%')
        expect(page.locator('#detail .probability')).to_contain_text('YES 75.0%')
        expect(page.locator('#detail .probability')).to_contain_text('As of')
        assert page.evaluate("entryConfidenceView({stale:true},latest.assets[0]).textContent") == 'Unavailable'
        assert page.evaluate("entryConfidenceView({stale:false},{markets:[]}).textContent") == 'Unavailable'
        assert page.evaluate("entryConfidenceView({stale:false},{markets:[{fresh:false,probability:{available:true,p_yes:.9,p_no:.1,timestamp:1}}]}).textContent") == 'Unavailable'
        for confidence, side in [(None, 'yes'), (1.2, 'yes'), (.8, None)]:
            assert page.evaluate("([confidence,side]) => entryConfidenceView({stale:false},{markets:[{fresh:true,probability:{available:true,p_yes:.9,p_no:.1,confidence,side,timestamp:1}}]}).textContent", [confidence,side]) == 'Unavailable'
        metrics = page.evaluate('summarize(latest.assets)')
        assert metrics['completed_trades'] == 1140
        assert metrics['breakeven_trades'] == 10
        assert metrics['win_rate'] == pytest.approx(996/1140)
        assert page.evaluate('summarize(latest.assets.slice(1)).realized_pnl') is None
        assert page.evaluate('summarize(latest.assets.map(a=>({...a,completed_trades:0,wins:0}))).win_rate') is None
        assert page.evaluate('leaders(latest.assets.map(a=>({...a,realized_pnl:1}))).length') == 7
        assert page.evaluate('leaders(latest.assets.map(a=>({...a,completed_trades:0}))).length') == 0
        assert page.evaluate('leaders(latest.assets.map(a=>({...a,realized_pnl:null}))).length') == 0
        page.locator('#comparison .market-button').filter(has_text='DOGE').click()
        expect(page.locator('#detail-title')).to_have_text('DOGE / Market detail')
        expect(page.locator('#detail .reference-row strong').first).to_have_text('$0.0971464')
        page.locator('#comparison .market-button').filter(has_text='ETH').click()
        expect(page.locator('#detail-title')).to_have_text('ETH / Market detail')
        expect(page.locator('#detail .probability')).to_contain_text('NO 60.0%')
        expect(page.locator('#market-tabs [data-asset=ETH]')).to_have_attribute('aria-pressed', 'true')
        expect(page.locator('#trades-title')).to_have_text('ETH · Latest 5 trades')
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
        state['view_fail'] = True
        page.wait_for_timeout(5200)
        expect(page.locator('#connection')).to_contain_text('Updates unavailable')
        expect(page.locator('#market-status .live-buying').first).to_have_text('Unavailable')
        expect(page.locator('#market-status .probability').first).to_have_text('Unavailable')
        expect(page.locator('#detail .probability')).not_to_contain_text('YES 75.0%')
        state['view_fail'] = False
        assert page.evaluate('localStorage.length') == 0
        assert page.evaluate('sessionStorage.length') == 0
        page.reload()
        expect(page.locator('#comparison .market-button')).to_have_count(10)
        for width, height, name in [(1440, 1000, 'desktop'), (768, 1024, 'tablet'), (390, 844, 'mobile')]:
            page.set_viewport_size({'width': width, 'height': height})
            assert page.evaluate('document.documentElement.scrollWidth') <= width
            if os.environ.get('PROJECT15_UI_SCREENSHOTS') and name != 'tablet':
                folder = ROOT / 'reports/public-ui'
                folder.mkdir(exist_ok=True)
                page.screenshot(path=str(folder / f'{name}.png'), full_page=True)
        # Repeated refreshes must preserve each mobile table's horizontal position
        # and the reader's vertical position, even when trade values change.
        page.locator('#trades').scroll_into_view_if_needed()
        page.evaluate("""() => {
            window.scrollContainers = [...document.querySelectorAll('.table-wrap')];
            for (const wrap of scrollContainers) wrap.scrollLeft = 120;
            window.scrollOffsets = scrollContainers.map(wrap => wrap.scrollLeft);
            window.readerY = scrollY;
        }""")
        assert page.evaluate('scrollOffsets.every(offset => offset > 0)')
        for _ in range(3):
            page.evaluate("""() => {
                const data = structuredClone(latest);
                data.assets.find(a => a.asset === selected).trades[0].net_pnl += 1;
                render(data);
            }""")
            page.wait_for_timeout(100)
            assert page.evaluate("""() => scrollContainers.every((wrap, i) =>
                wrap === document.querySelectorAll('.table-wrap')[i] &&
                Math.abs(wrap.scrollLeft - scrollOffsets[i]) < 1)""")
            assert abs(page.evaluate('scrollY - readerY')) < 2
        page.wait_for_timeout(1500)
        assert page.evaluate('scrollContainers.every((wrap, i) => Math.abs(wrap.scrollLeft - scrollOffsets[i]) < 1)')
        assert all(method == 'GET' for method, _ in requests)
        assert not any(path.startswith(('/api/stop', '/api/unlock', '/api/control')) for _, path in requests)
        assert not errors
        browser.close()
