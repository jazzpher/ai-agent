"""Local-only restyle checks with fake auth/providers/chat. No external calls."""
import sys, time, tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app, providers
from agent import AIAgent
from server_settings import launch_settings
from playwright.sync_api import sync_playwright

def run(out):
    out.mkdir(parents=True,exist_ok=True)
    pending={'text':None}; approvals=[]; saved=[]; received=[]
    def stream(self,message,uploaded_files_info=''):
        received.append((message,uploaded_files_info))
        if message=='stop-test':
            for i in range(20):
                if self.cancel_requested: break
                yield f'Waiting {i}'; time.sleep(.15)
            return
        yield 'Local fake reply completed.'
    def answer(session,approved): approvals.append(approved);pending['text']=None
    def save(items):saved.append(items)
    with patch.object(AIAgent,'chat_stream',stream), patch.object(AIAgent,'refresh_providers',lambda s:None), \
         patch.object(app.approval_gate,'pending',lambda s:pending['text']),patch.object(app.approval_gate,'answer',answer), \
         patch.object(providers,'save_providers',save),patch.object(providers,'test_connection',lambda *a:'MOCK connection OK'), \
         patch.object(providers,'list_models',lambda *a:['mock-model']), \
         patch.object(providers,'benchmark_model',lambda *a:{}),patch.object(providers,'format_benchmark',lambda *a:'MOCK benchmark done'):
        demo=app.build_app();settings=launch_settings({'AGENT_USERNAME':'preview','AGENT_PASSWORD':'local-preview-only-123'})
        settings.update(server_port=7862,inbrowser=False);demo.queue().launch(**settings,prevent_thread_lock=True,quiet=True)
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',args=['--no-sandbox'])
                for scheme in ('light','dark'):
                    page=browser.new_page(viewport={'width':390,'height':844},is_mobile=True,color_scheme=scheme)
                    page.route('**/*',lambda r:r.continue_() if r.request.url.startswith('http://127.0.0.1:7862') else r.abort())
                    page.goto('http://127.0.0.1:7862');page.locator('#workspace-login').wait_for()
                    assert page.request.get('http://127.0.0.1:7862/config').status==401
                    assert page.request.get('http://127.0.0.1:7862/gradio_api/info').status==401
                    page.get_by_label('username',exact=True).fill('preview');page.get_by_label('password',exact=True).fill('wrong')
                    page.get_by_role('button',name='Login',exact=True).click();page.get_by_text('Incorrect credentials',exact=False).wait_for()
                    page.screenshot(path=str(out/f'{scheme}-login-error.png'))
                    page.get_by_label('username',exact=True).fill('preview');page.get_by_label('password',exact=True).fill('local-preview-only-123');page.get_by_role('button',name='Login',exact=True).click()
                    page.locator('#message-input textarea').wait_for();field=page.locator('#message-input textarea')
                    page.locator('#examples').click();page.get_by_role('option',name='Help me outline a project plan',exact=True).click();page.wait_for_function("document.querySelector('#message-input textarea').value==='Help me outline a project plan'")
                    field.fill('stop-test');field.press('Enter');page.locator('#stop-button').wait_for();page.locator('#stop-button').click();page.locator('#send-button').wait_for();page.wait_for_function("document.querySelector('#message-input textarea').value===''")
                    page.locator('#clear-button').click();page.locator('.empty-chat').wait_for()
                    for approved,name in ((True,'Approve'),(False,'Deny')):
                        pending['text']='FAKE LOCAL PREVIEW: install example';page.get_by_role('button',name=name,exact=True).wait_for();page.screenshot(path=str(out/f'{scheme}-approval.png'));page.get_by_role('button',name=name,exact=True).click();page.locator('#approval-banner').wait_for(state='hidden');assert approvals[-1] is approved
                    with tempfile.NamedTemporaryFile(suffix='.txt') as f:
                        f.write(b'Local test attachment.');f.flush();
                        with page.expect_file_chooser() as chooser:
                            page.locator('#upload-button').click()
                        chooser.value.set_files(f.name)
                        page.wait_for_function("!document.querySelector('#file-status textarea').value.includes('No files')")
                        field.fill('attachment test');page.locator('#send-button').click();page.get_by_text('Local fake reply completed.',exact=False).wait_for();page.locator('#send-button').wait_for();assert 'Uploaded files:' in received[-1][0]
                        assert 'No files' in page.locator('#file-status textarea').input_value()
                    page.get_by_role('tab',name='Settings',exact=True).click();page.get_by_label('Enable Nemotron thinking',exact=True).check()
                    page.get_by_text('Providers & API keys',exact=True).click();page.get_by_text('Provider 1 -',exact=False).first.click()
                    assert page.get_by_label('API key',exact=True).first.get_attribute('type')=='password'
                    page.get_by_label('Base URL',exact=True).first.fill('https://mock.invalid/v1');page.get_by_label('Model',exact=True).first.fill('mock-model');page.get_by_label('API key',exact=True).first.fill('fake-local-only-key')
                    page.get_by_role('button',name='Test connection',exact=True).first.click();page.get_by_text('MOCK connection OK',exact=True).wait_for()
                    page.get_by_role('button',name='Fetch models',exact=True).first.click();page.get_by_text('1 models.',exact=False).wait_for()
                    page.get_by_role('button',name='Benchmark (5 calls)',exact=True).first.click();page.get_by_text('MOCK benchmark done',exact=True).wait_for()
                    page.get_by_role('button',name='Save providers',exact=True).click();page.get_by_text('Saved.',exact=True).wait_for();assert saved[-1][0]['model']=='mock-model';assert page.get_by_label('API key',exact=True).first.input_value()==''
                    page.screenshot(path=str(out/f'{scheme}-settings.png'));page.close();print(scheme,'login error/success, starter, stop, clear, approve/deny, upload, settings/test/models/benchmark/save PASS')
                browser.close()
        finally:demo.close()
if __name__=='__main__':run(Path(sys.argv[1]))
