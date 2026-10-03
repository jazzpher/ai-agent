"""Local long-text checks for smaller mobile chat/composer text, no model traffic."""
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
from agent import AIAgent
from playwright.sync_api import sync_playwright

def run(out):
    out.mkdir(parents=True,exist_ok=True)
    def stream(self,message,uploaded_files_info=''):
        yield 'A long reply stays readable and wraps inside the conversation. ' * 20 + '\n\n' + 'longword' * 80
    with patch.object(AIAgent,'chat_stream',stream),patch.object(AIAgent,'refresh_providers',lambda s:None):
        demo=app.build_app();demo.queue().launch(server_name='127.0.0.1',server_port=7863,prevent_thread_lock=True,quiet=True)
        try:
            with sync_playwright() as p:
                b=p.chromium.launch(executable_path='/usr/bin/google-chrome',args=['--no-sandbox'])
                for scheme in ('light','dark'):
                    for width in (320,390):
                        pg=b.new_page(viewport={'width':width,'height':844},is_mobile=True,color_scheme=scheme)
                        pg.route('**/*',lambda r:r.continue_() if r.request.url.startswith('http://127.0.0.1:7863') else r.abort())
                        pg.goto('http://127.0.0.1:7863');field=pg.locator('#message-input textarea');field.wait_for()
                        def fit():
                            assert pg.evaluate('document.documentElement.scrollWidth<=innerWidth')
                            box=pg.locator('#composer').bounding_box();assert box['y']+box['height']<=845
                        fit();pg.screenshot(path=str(out/f'{scheme}-{width}-placeholder.png'))
                        pg.locator('#examples').click();pg.get_by_role('option',name='I-search mo kung paano gumawa ng FastAPI app, tapos basahin mo yung top result',exact=True).click()
                        pg.wait_for_function("document.querySelector('#message-input textarea').value.startsWith('I-search')")
                        pg.wait_for_timeout(200);field.evaluate('e=>e.scrollTop=0');fit();pg.screenshot(path=str(out/f'{scheme}-{width}-starter.png'))
                        text=('Please explain this longer question without cutting anything off. '*10)+'\nSecond line, with '+('unbrokenword'*30)
                        field.fill(text);pg.wait_for_timeout(200);fit()
                        assert field.evaluate("e=>getComputedStyle(e).whiteSpace")== 'pre-wrap'
                        assert field.evaluate('e=>e.scrollWidth<=e.clientWidth+1')
                        assert field.input_value()==text
                        field.evaluate('e=>e.scrollTop=0')
                        pg.screenshot(path=str(out/f'{scheme}-{width}-long-prompt.png'))
                        pg.locator('#send-button').click();pg.get_by_text('A long reply stays readable',exact=False).first.wait_for();pg.locator('#send-button').wait_for();fit()
                        assert pg.locator('#agent-chat .message').first.evaluate('e=>getComputedStyle(e).fontSize')=='13px'
                        pg.locator('#agent-chat .bubble-wrap').evaluate('e=>e.scrollTop=e.scrollHeight');pg.screenshot(path=str(out/f'{scheme}-{width}-long-chat.png'));pg.close();print(scheme,width,'placeholder/starter/multiline and unbroken prompt/long reply/fit PASS')
                b.close()
        finally:demo.close()
if __name__=='__main__':run(Path(sys.argv[1]))
