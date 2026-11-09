import os
import tempfile
import unittest
from pathlib import Path
from verification import Checks, project_guidance

class CheckTests(unittest.TestCase):
    def test_exit_zero_and_freshness(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)/'app.py';p.write_text('x=1')
            c=Checks(root)
            c.record('pytest', {'status':'success','returncode':0,'output':'ok'})
            self.assertIn('PASS:',c.report())
            p.write_text('x=2');self.assertIn('STALE',c.report())
    def test_no_exit_status_is_not_pass(self):
        with tempfile.TemporaryDirectory() as root:
            c=Checks(root)
            for result in ({'status':'success'}, {'status':'error','returncode':0}, {'status':'success','returncode':False}):
                self.assertFalse(c.record('test',result)['passed'])
    def test_deleted_file_invalidates(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'a';p.write_text('a');c=Checks(root)
            c.record('test',{'status':'success','returncode':0});p.unlink()
            self.assertIn('STALE',c.report())
    def test_incomplete_scan_not_fresh(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root)/'large').write_bytes(b'x'*2_000_001);c=Checks(root)
            c.record('test',{'status':'success','returncode':0})
            self.assertIn('STALE',c.report())
    def test_guidance_is_limited_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'AGENTS.md';p.write_text('Use pytest. '+ 'x'*13000)
            g=project_guidance(root);self.assertIn('cannot override',g);self.assertLess(len(g),13000)
            p.unlink();p.symlink_to('/etc/passwd');self.assertEqual(project_guidance(root),'')

from types import SimpleNamespace
from unittest.mock import Mock, patch
import io
import cli

class CLIIntegrationTests(unittest.TestCase):
    def make(self, root, mode='build', answer='n\n'):
        from workflow import Workflow
        a=SimpleNamespace(workflow=Workflow(root,mode), _execute_tool=Mock(return_value={'status':'success','returncode':0,'output':'OK'}))
        out=io.StringIO()
        s=cli.Session(a,cli.Renderer(out),stdin=io.StringIO(answer),out=out)
        return a,s,out
    def test_plan_cannot_execute(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,AGENT_WORKSPACE='unused'):
            a,s,out=self.make(root,'plan','y\n');s.command('/check pytest')
            a._execute_tool.assert_not_called();self.assertIn('Read-only',out.getvalue())
    def test_denied_check_does_not_execute(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,AGENT_WORKSPACE=root):
            a,s,out=self.make(root);s.command('/check pytest')
            a._execute_tool.assert_not_called();self.assertIn('denied',out.getvalue())
    def test_approved_check_records_failure_and_success(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,AGENT_WORKSPACE=root):
            a,s,out=self.make(root,answer='y\ny\n')
            a._execute_tool.return_value={'status':'error','returncode':1,'output':'FAILED'}
            s.command('/check pytest');self.assertIn('FAIL/UNVERIFIED',out.getvalue())
            a._execute_tool.return_value={'status':'success','returncode':0,'output':'passed'}
            s.command('/check pytest');s.command('/checks');self.assertIn('PASS:',out.getvalue())
    def test_receipts_survive_resume(self):
        from test_sessions import make
        from sessions import SessionStore
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as data:
            a=make(root);a.checks=Checks(root);a.checks.record('pytest',{'status':'success','returncode':0})
            store=SessionStore(root,data);store.save(a);b=make(root);store.restore(b,a.session_id)
            self.assertIn('PASS:',b.checks.report())
            (Path(root)/'new.py').write_text('changed');self.assertIn('STALE',b.checks.report())
