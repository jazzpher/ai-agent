"""Opt-in local Chrome check for docs/index.html. Fake app server only; no Render, no provider traffic.
Run: python checks/wake_page_check.py --output /tmp/wake-screenshots
Requires playwright and a local Chrome (not a production dependency).
"""
import argparse
import http.server
import threading
import time
from functools import partial
from pathlib import Path
from playwright.sync_api import sync_playwright

DOCS = Path(__file__).resolve().parents[1] / 'docs'
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8"/></svg>'


class FakeApp(http.server.BaseHTTPRequestHandler):
    awake_at = float('inf')
    hits = 0
    def do_GET(self):
        FakeApp.hits += 1
        if self.path.startswith('/favicon.ico') and time.time() >= FakeApp.awake_at:
            self.send_response(200); self.send_header('Content-Type', 'image/svg+xml'); self.end_headers(); self.wfile.write(SVG)
        elif self.path.startswith('/favicon.ico'):
            # Mimics a sleeping host's HTML "starting" page: an image probe must treat it as not ready.
            self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers(); self.wfile.write(b'<html>starting</html>')
        else:
            self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers(); self.wfile.write(b'<html><body id="app">APP READY</body></html>')
    def log_message(self, *a): pass


def serve(handler):
    s = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    app = serve(FakeApp)
    site = serve(partial(http.server.SimpleHTTPRequestHandler, directory=str(DOCS)))
    url = f'http://127.0.0.1:{site.server_port}/index.html?app=http://127.0.0.1:{app.server_port}/'
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome')
        for name, w, h in (('phone', 390, 844), ('small', 320, 700), ('desktop', 1280, 800)):
            ctx = browser.new_context(viewport={'width': w, 'height': h}, device_scale_factor=2 if w < 500 else 1)
            page = ctx.new_page()
            FakeApp.awake_at = float('inf')
            page.goto(url)
            page.wait_for_function("document.getElementById('elapsed').textContent !== '0:00'", timeout=5000)
            assert page.inner_text('h1') == 'Waking up the server...', page.inner_text('h1')
            assert page.url.startswith('http://127.0.0.1:%d/' % site.server_port), 'must not leave while HTML probe answers'
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{name}: horizontal overflow'
            assert page.is_visible('#steps') and page.is_visible('#elapsed')
            page.screenshot(path=str(output / f'{name}-waking.png'))
            # Wake the fake app: the page must redirect on its own, no refresh.
            FakeApp.awake_at = time.time()
            page.wait_for_selector('#title:has-text("Server is awake")', timeout=8000)
            page.screenshot(path=str(output / f'{name}-ready.png'))
            page.wait_for_selector('#app', timeout=5000)
            assert page.url.startswith(f'http://127.0.0.1:{app.server_port}/'), page.url
            ctx.close()
        # Slow state, simulated clock; open link and Try again must be present.
        ctx = browser.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2)
        page = ctx.new_page(); page.clock.install()
        FakeApp.awake_at = float('inf')
        page.goto(url)
        page.clock.run_for(190000)
        page.wait_for_selector('#eyebrow:has-text("TAKING LONGER")', timeout=5000)
        assert page.is_visible('#open')
        page.screenshot(path=str(output / 'phone-slow.png'))
        page.clock.run_for(450000)
        page.wait_for_selector('#retry:visible', timeout=5000)
        page.screenshot(path=str(output / 'phone-giveup.png'))
        ctx.close()
        # A non-loopback ?app= override must be ignored.
        ctx = browser.new_context(); page = ctx.new_page()
        page.route('**/favicon.ico*', lambda r: r.abort())
        page.goto(f'http://127.0.0.1:{site.server_port}/index.html?app=https://evil.example/')
        assert page.get_attribute('#open', 'href') == 'https://private-ai-agent-9d5u.onrender.com/'
        ctx.close(); browser.close()
    print('wake page checks passed; screenshots in', output)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--output', type=Path, default=Path('/tmp/wake-screenshots'))
    run(ap.parse_args().output)
