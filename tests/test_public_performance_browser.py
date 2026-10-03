"""Public daily-history UI with synthetic GET-only APIs."""
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest

from btc15.public_performance import build_performance, trade_day
from btc15.public_site import ASSETS

ROOT = Path(__file__).resolve().parents[1]


def test_daily_performance_navigation_comparison_history_and_mobile():
    pw = pytest.importorskip('playwright.sync_api')
    now = datetime(2026, 9, 29, 12, tzinfo=ZoneInfo('America/New_York')).timestamp()
    closed = datetime(2026, 9, 28, 12, tzinfo=ZoneInfo('America/New_York')).timestamp()
    rows = [dict(market='BTC-DAY-'+str(i), opened=closed-86400, exit_timestamp=closed,
                 status='CLOSED', net_pnl=1, entry=.5, exit=.8, fees=.01, side='yes', bought=1)
            for i in range(38)]
    histories = {a: dict(rows=rows if a == 'BTC' else [], total=len(rows) if a == 'BTC' else 0, updated_at=now) for a in ASSETS}
    histories['ETH']['rows'] = [dict(market='ETH-OPEN', status='OPEN', opened=closed-40*86400)]
    histories['ETH']['total'] = 1
    data = build_performance(histories, ASSETS, now)
    state = {'fail': False}
    errors = []
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.on('pageerror', lambda error: errors.append(str(error)))

        def route(r):
            assert r.request.method == 'GET'
            url = urlparse(r.request.url)
            if url.path == '/api/performance':
                return r.fulfill(status=503 if state['fail'] else 200, json=data)
            if url.path.startswith('/api/history/'):
                asset = url.path.rsplit('/', 1)[-1]
                q = parse_qs(url.query)
                field = 'opened' if q['basis'][0] == 'opened' else 'exit_timestamp'
                selected = [x for x in histories[asset]['rows'] if trade_day(x.get(field)) == q['day'][0]]
                offset = int(q['offset'][0])
                return r.fulfill(json=dict(rows=selected[offset:offset+25], total=len(selected), stale=False))
            filename = 'performance.html' if url.path == '/performance' else url.path.lstrip('/')
            mime = 'image/svg+xml' if filename.endswith('.svg') else 'text/javascript' if filename.endswith('.js') else 'text/css' if filename.endswith('.css') else 'text/html'
            return r.fulfill(body=(ROOT / 'src/btc15/public_static' / filename).read_text(), content_type=mime)

        page.route('**/*', route)
        page.goto('http://ui.test/performance')
        expect = pw.expect
        expect(page.locator('#all-time-summary')).to_contain_text('+$38.00')
        expect(page.locator('#daily-summary')).to_contain_text('-$38.00')
        chart = page.locator('#pnl-chart svg')
        expect(chart).to_contain_text('Cumulative P&L (USD)')
        expect(chart).to_contain_text('Date (America/New_York)')
        chart.focus()
        page.keyboard.press('End')
        page.keyboard.press('ArrowLeft')
        expect(page.locator('#chart-readout')).to_contain_text('Sep 28, 2026')
        expect(page.locator('#chart-readout')).to_contain_text('Day: +$38.00 USD')
        page.keyboard.press('Enter')
        expect(page.locator('#selected-day')).to_have_value('2026-09-28')
        expect(chart).to_be_focused()
        page.locator('#today').click()
        page.locator('#selected-strip-prev').click()
        expect(page.locator('#selected-strip [data-date="2026-09-16"]')).to_be_visible()
        page.locator('#selected-strip-next').click()
        page.locator('#selected-strip [data-date="2026-09-28"]').click()
        expect(page.locator('#selected-day')).to_have_value('2026-09-28')
        expect(page.locator('#selected-strip [data-date="2026-09-28"]')).to_have_attribute('aria-pressed', 'true')
        page.locator('#compare-strip [data-date="2026-09-26"]').click()
        expect(page.locator('#compare-day')).to_have_value('2026-09-26')

        page.locator('#selected-jump').click()
        expect(page.locator('#date-calendar')).to_be_visible()
        page.locator('#calendar-month-prev').click()
        expect(page.locator('#calendar-month')).to_have_text('August 2026')
        page.locator('#calendar-month-next').click()
        expect(page.locator('#calendar-month')).to_have_text('September 2026')
        expect(page.locator('#calendar-days [data-date="2026-09-30"]')).to_be_disabled()

        page.locator('#calendar-days [data-date="2026-09-28"]').click()
        expect(page.locator('#daily-summary')).to_contain_text('+$38.00')
        expect(page.locator('#day-trades tr')).to_have_count(25)
        page.locator('#day-trades-next').click()
        expect(page.locator('#day-trades-page')).to_have_text('26–38 of 38')
        page.locator('#trade-basis').select_option('opened')
        expect(page.locator('#day-trades-message')).to_have_text('No trades opened on this day.')
        page.locator('#selected-jump').click()
        page.locator('#calendar-days [data-date="2026-09-27"]').click()
        expect(page.locator('#day-trades tr')).to_have_count(25)
        page.locator('#trade-asset').select_option('ETH')
        expect(page.locator('#day-trades tr')).to_have_count(0)
        expect(page.locator('#day-trades-title')).to_contain_text('ETH')
        for width in (1440, 768, 390):
            page.set_viewport_size({'width': width, 'height': 900})
            assert page.evaluate('document.documentElement.scrollWidth') <= width
            assert page.locator('table th, table td').evaluate_all("cells => cells.every(cell => getComputedStyle(cell).textAlign === 'center')")
            hit = page.locator('.pnl-hit')
            hit.hover(position={'x': 10, 'y': 20})
            expect(page.locator('#chart-readout')).to_contain_text('Aug 19, 2026')
            hit.click(position={'x': 10, 'y': 20})
            expect(page.locator('#selected-day')).to_have_value('2026-08-19')
            chart.focus()
            page.keyboard.press('End')
            page.locator('#chart-readout button').click()
            expect(page.locator('#selected-day')).to_have_value('2026-09-29')
            page.locator('#compare-jump').click()
            expect(page.locator('#date-calendar')).to_be_visible()
            assert page.locator('#date-calendar').bounding_box()['width'] <= width
            page.keyboard.press('Escape')
            expect(page.locator('#date-calendar')).not_to_be_visible()
            expect(page.locator('#compare-jump')).to_be_focused()
            page.locator('#selected-strip [aria-pressed=true]').focus()
            page.keyboard.press('Enter')
            expect(page.locator('#selected-strip [aria-pressed=true]')).to_be_visible()

            expect(page.get_by_role('link', name='Market dashboard', exact=True)).to_be_visible()
            expect(page.get_by_role('link', name='Performance history', exact=True)).to_be_visible()
        for width in (1440, 390):
            page.set_viewport_size({'width': width, 'height': 900})
            for column in range(5):
                page.locator('#today').click()
                row = page.locator('#daily-log tr').filter(has=page.get_by_role('button', name='Sep 28, 2026', exact=True))
                row.locator('td').nth(column).click()
                expect(page.locator('#selected-day')).to_have_value('2026-09-28')
                expect(row).to_have_class('selected')
            for column in range(10):
                page.locator('#trade-asset').select_option('BTC')
                row = page.locator('#performance-markets tr').filter(has=page.get_by_role('button', name='ETH', exact=True))
                row.locator('td').nth(column).click()
                expect(page.locator('#trade-asset')).to_have_value('ETH')
                expect(row).to_have_class('selected')
                expect(page.locator('#day-trades-title')).to_contain_text('ETH')
            page.locator('#performance-totals td').first.click()
            expect(page.locator('#trade-asset')).to_have_value('ETH')
        page.locator('#performance-markets').get_by_role('button', name='BTC', exact=True).focus()
        page.keyboard.press('Enter')
        expect(page.locator('#trade-asset')).to_have_value('BTC')
        state['fail'] = True
        page.evaluate('refreshPerformance()')
        expect(page.locator('#performance-message')).to_contain_text('Previously loaded results may be out of date')
        expect(page.locator('#retry-performance')).to_be_visible()
        state['fail'] = False
        data['stale'] = True
        page.locator('#retry-performance').click()
        expect(page.locator('#performance-message')).to_contain_text('History updates delayed')
        assert not errors
        browser.close()
