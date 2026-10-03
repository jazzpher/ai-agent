"""Opt-in local Chrome checks. No provider traffic; all answers and approvals are fake.
Run: python checks/ui_browser_check.py --output /tmp/ui-screenshots
Requires playwright and a local Chrome (not a production dependency).
"""
import argparse
from pathlib import Path
import sys
import time
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from agent import AIAgent
from playwright.sync_api import sync_playwright


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    pending = {'text': None}
    answers = []
    def stream(self, message, uploaded_files_info=''):
        yield 'Model is thinking; waiting for answer...'
        time.sleep(.6)
        if message == 'long':
            yield '\n\n'.join(f'Paragraph {i}: This is a long reply to check scrolling and the composer.' for i in range(35)) + '\n\n```python\n' + 'x' * 200 + '\n```'
        else:
            yield "Hello! Let's make something useful.\n\nI can help you **write**, explore an idea, or work with a file.\n\n```python\nprint('Ready when you are')\n```\n\nAno ang gusto mong gawin?"
    def answer(session, approved):
        answers.append(approved)
        pending['text'] = None
    with patch.object(AIAgent, 'chat_stream', stream), patch.object(AIAgent, 'refresh_providers', lambda self: None), \
         patch.object(app.approval_gate, 'pending', lambda session: pending['text']), \
         patch.object(app.approval_gate, 'answer', answer):
        demo = app.build_app()
        demo.queue().launch(server_name='127.0.0.1', server_port=7860, prevent_thread_lock=True, quiet=True)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox'])
                for name, w, h in [('small',320,740), ('phone',390,844), ('desktop',1280,900)]:
                    page = browser.new_page(viewport={'width':w,'height':h},device_scale_factor=1)
                    page.route('**/*', lambda route: route.continue_() if route.request.url.startswith('http://127.0.0.1:7860') else route.abort())
                    page.goto('http://127.0.0.1:7860')
                    page.locator('#message-input textarea').wait_for()
                    page.wait_for_timeout(300)
                    def fit():
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name + ' horizontal overflow'
                        box = page.locator('#composer').bounding_box()
                        assert box['y'] + box['height'] <= h + 1, (name, 'composer offscreen', box)
                    fit()
                    page.screenshot(path=str(output/f'{name}-empty.png'))
                    field = page.locator('#message-input textarea')
                    field.fill('hello'); field.press('Enter')
                    page.locator('#stop-button').wait_for(state='visible')
                    assert page.locator('#stop-button').inner_text() == 'Stop'
                    page.screenshot(path=str(output/f'{name}-waiting.png'))
                    page.get_by_text('Ano ang gusto mong gawin?',exact=False).wait_for()
                    page.locator('#send-button').wait_for(state='visible')
                    assert field.input_value() == ''
                    fit(); page.screenshot(path=str(output/f'{name}-chat.png'))
                    page.get_by_role('tab',name='Settings',exact=True).click()
                    page.get_by_label('Enable Nemotron thinking',exact=True).check()
                    page.screenshot(path=str(output/f'{name}-settings.png'))
                    page.get_by_text('Providers & API keys',exact=True).click()
                    page.get_by_text('Provider 1 -',exact=False).first.click()
                    assert page.get_by_label('API key',exact=True).first.get_attribute('type') == 'password'
                    page.screenshot(path=str(output/f'{name}-provider.png'))
                    page.get_by_role('tab',name='Chat',exact=True).click()
                    pending['text'] = 'FAKE PREVIEW ONLY: pip install example-package'
                    page.locator('#approval-banner').wait_for(state='visible')
                    page.get_by_role('button',name='Deny',exact=True).wait_for()
                    fit(); page.screenshot(path=str(output/f'{name}-approval.png'))
                    page.get_by_role('button',name='Deny',exact=True).click()
                    page.locator('#approval-banner').wait_for(state='hidden')
                    assert answers[-1] is False
                    field.fill('long'); page.locator('#send-button').click()
                    page.get_by_text('Paragraph 34:',exact=False).wait_for()
                    page.locator('#send-button').wait_for(state='visible')
                    page.wait_for_timeout(500)
                    fit()
                    scroll = page.locator('#agent-chat .bubble-wrap')
                    assert scroll.evaluate('(e)=>e.scrollHeight > e.clientHeight'), name + ' long reply must scroll'
                    scroll.evaluate('(e)=>e.scrollTop=e.scrollHeight')
                    page.screenshot(path=str(output/f'{name}-long.png'))
                    page.locator('#clear-button').click()
                    page.locator('.empty-chat').wait_for()
                    fit()
                    page.get_by_role('tab',name='Session',exact=True).click()
                    page.get_by_text('not started.',exact=False).first.wait_for()
                    page.close()
                    print(f'{name}: empty, enter/send, waiting/stop, reply, settings, password, approval/deny, long reply, clear and session PASS')
                browser.close()
        finally:
            demo.close()

if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,default=Path('/tmp/ui-screenshots'))
    run(parser.parse_args().output)
