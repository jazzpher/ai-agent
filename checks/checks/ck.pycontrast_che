"""Opt-in mobile palette checks using local fake chat only, no provider traffic."""
import sys, argparse, http.server, threading
from pathlib import Path
from functools import partial
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
from agent import AIAgent
from ui_design import LOGIN_MESSAGE
from playwright.sync_api import sync_playwright

def run(out, enforce=True):
    out.mkdir(parents=True, exist_ok=True)
    def stream(self, message, uploaded_files_info=''):
        yield 'Hello! This reply should be readable.\n\n**Markdown** and `code` stay visible.\n\n```python\nprint("hello")\n```'
    with patch.object(AIAgent,'chat_stream',stream), patch.object(AIAgent,'refresh_providers',lambda s:None):
        demo=app.build_app(); demo.queue().launch(server_name='127.0.0.1',server_port=7860,prevent_thread_lock=True,quiet=True)
        auth=app.build_app(); auth.launch(server_name='127.0.0.1',server_port=7861,auth=('preview','local-only'),auth_message=LOGIN_MESSAGE,prevent_thread_lock=True,quiet=True)
        site=http.server.ThreadingHTTPServer(('127.0.0.1',0),partial(http.server.SimpleHTTPRequestHandler,directory=str(Path(__file__).resolve().parents[1]/'docs')))
        threading.Thread(target=site.serve_forever,daemon=True).start()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',args=['--no-sandbox'])
                for scheme in ('light','dark'):
                    page=browser.new_page(viewport={'width':390,'height':844},is_mobile=True,color_scheme=scheme)
                    page.route('**/*',lambda r:r.continue_() if r.request.url.startswith('http://127.0.0.1:') else r.abort())
                    def check(name):
                        page.screenshot(path=str(out/f'{scheme}-{name}.png'))
                        failures=page.evaluate('''() => {
                          const rgb=s=>(s.match(/[\\d.]+/g)||[]).slice(0,3).map(Number);
                          const lum=c=>c.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
                          return [...document.querySelectorAll('p,h1,h2,label,input,textarea,button,code,code span,.eyebrow,.foot,.steps li,.sub,.brand strong')].filter(e=>e.getClientRects().length && !['checkbox','radio','hidden'].includes(e.type) && !e.closest('[aria-hidden="true"]') && (e.textContent.trim()||e.tagName==='INPUT'||e.tagName==='TEXTAREA')).flatMap(e=>{
                            let x=e,bg; while(x){bg=getComputedStyle(x).backgroundColor;if(!bg.includes('rgba')&&!bg.includes('transparent'))break;x=x.parentElement;}
                            if(!x)return [];const fg=getComputedStyle(e).color;
                            let a=lum(rgb(fg)),b=lum(rgb(bg)),ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
                            return ratio<4.5?[{text:e.textContent.trim().slice(0,55),fg,bg,ratio}]:[];
                          });
                        }''')
                        print(scheme,name,failures)
                        if enforce: assert not failures,(scheme,name,failures)
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), name
                    page.goto('http://127.0.0.1:7861');page.get_by_role('button',name='Login',exact=True).wait_for();check('login')
                    page.goto('http://127.0.0.1:7860');page.locator('#message-input textarea').wait_for();check('empty')
                    field=page.locator('#message-input textarea');field.fill('hello');field.press('Enter')
                    page.get_by_text('This reply should be readable.',exact=False).wait_for();page.locator('#send-button').wait_for(state='visible');check('chat')
                    page.get_by_role('tab',name='Settings',exact=True).click();page.get_by_label('Enable Nemotron thinking',exact=True).wait_for();check('settings')
                    page.get_by_text('Providers & API keys',exact=True).click();page.get_by_text('Provider 1 -',exact=False).first.click();page.get_by_label('API key',exact=True).first.wait_for();check('provider')
                    page.get_by_role('tab',name='Session',exact=True).click();page.get_by_text('Sandbox: not started.',exact=False).first.wait_for();check('session')
                    page.route('**/favicon.ico*',lambda r:r.abort())
                    page.goto(f'http://127.0.0.1:{site.server_port}/index.html?app=http://127.0.0.1:7860/');page.locator('#title').wait_for();check('wake')
                    page.close()
                browser.close()
        finally:
            demo.close();auth.close();site.shutdown()
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);ap.add_argument('--before',action='store_true');a=ap.parse_args();run(a.output,not a.before)
