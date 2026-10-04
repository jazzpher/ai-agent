"""Artifact preview, streaming public code and tool detail regressions. No network."""
import base64,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import worklog,file_preview,app
class ArtifactUXTest(unittest.TestCase):
 def setUp(self): self.d=Path(tempfile.mkdtemp())
 def test_html_card_has_safe_open_envelope(self):
  p=self.d/'index.html';p.write_text('<title>Demo</title><h1>Hi</h1><script>document.body.dataset.ready="yes"</script>')
  out=worklog.file_card(str(p),'index.html')
  self.assertIn('fc-open',out);self.assertNotIn('<script>',out);self.assertNotIn('fc-pre',out)
  import re
  payload=json.loads(base64.b64decode(re.search(r'value="([^"]+)"',out)[1]))
  self.assertIn('<h1>Hi</h1>',payload['html']);self.assertTrue(payload['web'])
 def test_markdown_renders_but_escapes_html(self):
  p=self.d/'README.md';p.write_text('# Hello\n\n**bold**\n\n<script>bad()</script>')
  out=file_preview.render_preview(str(p))
  self.assertIn('<h1>Hello</h1>',out);self.assertIn('<strong>bold</strong>',out);self.assertNotIn('<script>',out)
 def test_assets_bundle_but_never_escape_directory(self):
  (self.d/'x.png').write_bytes(b'png');(self.d/'style.css').write_text('body{color:red}')
  (self.d/'app.js').write_text('console.log("ok")')
  p=self.d/'index.html';p.write_text('<link rel="stylesheet" href="style.css"><img src="x.png"><script src="app.js"></script><img src="../private.png">')
  out=file_preview.webpage_source(str(p))
  self.assertIn('data:image/png;base64',out);self.assertIn('<style>body',out);self.assertIn('console.log',out)
  self.assertIn('../private.png',out)
 def test_public_partial_code_and_split_escapes(self):
  args='{"path":"demo/index.html","content":"a\\nb\\"c\\u00'
  out=worklog.writing_activity({0:{'name':'write_file','arguments':args}})
  self.assertIn('Writing demo/index.html',out);self.assertIn('   2  b&quot;c',out)
  self.assertEqual(worklog.writing_activity({0:{'name':'read_file','arguments':args}}),'')
  self.assertNotIn('<script>',worklog.writing_activity({0:{'name':'write_file','arguments':'{"content":"<script>"'}}))
 def test_detailed_command_and_output_escaped(self):
  marker=worklog.tool_detail({'command':'echo "<hello>"','api_key':'secret'}, {'output':'one\ntwo<script>'})
  out=worklog.render('🔧 **Action:** `run_bash`\n\n✅ `run_bash` — 0.12s — ok\n'+marker+'\n---\n')
  self.assertIn('wl-box',out);self.assertIn('Copy',out);self.assertIn('one\ntwo&lt;script&gt;',out)
  self.assertNotIn('secret',out);self.assertNotIn('[[detail:',out)
 def test_open_rejects_path_traversal(self):
  with patch.object(app,'WORKSPACE_DIR',str(self.d)):
   self.assertFalse(app.open_artifact('../../etc/passwd')[0]['visible'])
 def test_live_workspace_quiet_tick_no_redraw(self):
  from agent import AIAgent
  a=AIAgent(api_key='fake');a._ui_running=True
  with patch.object(app,'WORKSPACE_DIR',str(self.d)):
   app.live_workspace(a,'');self.assertEqual(app.live_workspace(a,''),(app.gr.update(),app.gr.update()))
   (self.d/'new.html').write_text('hi');self.assertIn('new.html',app.live_workspace(a,'')[1])
 def test_new_html_schedules_auto_open(self):
  import time
  from agent import AIAgent
  a=AIAgent(api_key='fake')
  def demo(*args):
   (self.d/'new.html').write_text('hi')
   yield [{'role':'assistant','content':'done'}],'metrics'
  with patch.object(app,'WORKSPACE_DIR',str(self.d)),patch.object(app,'chat_stream',demo):
   app.begin_background_chat('test',[],[],a)
   for _ in range(50):
    if not a._ui_running:break
    time.sleep(.01)
  self.assertEqual(a._ui_autoopen,str(self.d/'new.html'))
