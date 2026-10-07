import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from kernel_sandbox import available, run

@unittest.skipUnless(available(), 'bubblewrap kernel support required')
class IsolationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.workspace = cls.temp.name + '/work'
        cls.venv = cls.temp.name + '/venv'
        os.mkdir(cls.workspace)
        subprocess.run([__import__('sys').executable, '-m', 'venv', '--system-site-packages', cls.venv],check=True)
    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()
    def python(self, code, timeout=5):
        return run([self.venv+'/bin/python','-c',code],self.workspace,self.venv,timeout)
    def test_write_workspace(self):
        self.assertEqual(self.python("open('ok','w').write('yes');print('done')")['status'],'success')
    def test_secrets_scrubbed(self):
        with patch.dict(os.environ, {'SANDBOX_TEST_SECRET':'do-not-inherit'}):
            self.assertEqual(self.python("import os;print(os.getenv('SANDBOX_TEST_SECRET'))")['output'],'None')
    def test_parent_files_invisible(self):
        for path in ['/etc/passwd','/app/app.py','/proc/1/root/app/app.py','/proc/1/environ']:
            r=self.python(f"print(open({path!r}).read())")
            # PID 1 is bubblewrap's clean process, never the host app.
            if path != '/proc/1/environ': self.assertEqual(r['status'],'error',r)
            else: self.assertNotIn('SANDBOX_TEST_SECRET',r['output'])
    def test_network_denied(self):
        r=self.python("import socket;socket.create_connection(('1.1.1.1',80),1)")
        self.assertEqual(r['status'],'error')
    def test_symlink_escape_denied(self):
        self.assertEqual(self.python("import os;os.symlink('/etc/passwd','escape');open('escape').read()")['status'],'error')
    def test_runtime_read_only(self):
        self.assertEqual(self.python("open('/usr/escape','w').write('x')")['status'],'error')
    def test_nested_namespace_denied(self):
        r=run(['/usr/bin/unshare','-Ur','true'],self.workspace,self.venv,5)
        self.assertEqual(r['status'],'error')
    def test_timeout(self):
        self.assertIn('Timed out',self.python('import time;time.sleep(10)',0.1)['output'])
    def test_memory_limit(self):
        self.assertEqual(self.python('a=bytearray(700*1024*1024)')['status'],'error')
    def test_output_cap(self):
        self.assertIn('Output capped',self.python("print('x'*100000)")['output'])

class ApprovalTest(unittest.TestCase):
    def test_sandbox_commands_but_not_install_skip_approval(self):
        from approvals import needs_approval
        with patch.dict(os.environ,{'AGENT_APPROVAL':'auto'}):
            self.assertFalse(needs_approval('run_bash','risky','bubblewrap'))
            self.assertFalse(needs_approval('run_python','risky','bubblewrap'))
            self.assertTrue(needs_approval('pip_install','safe','bubblewrap'))

@unittest.skipUnless(__import__('kernel_sandbox').landlock_available(), 'Landlock ABI 3+ required')
class LandlockIsolationTest(IsolationTest):
    def python(self, code, timeout=5):
        from kernel_sandbox import landlock_run
        return landlock_run([self.venv+'/bin/python','-c',code],self.workspace,self.venv,timeout)
    def test_nested_namespace_denied(self):
        from kernel_sandbox import landlock_run
        self.assertEqual(landlock_run(['/usr/bin/unshare','-Ur','true'],self.workspace,self.venv,5)['status'],'error')
    def test_signal_parent_denied(self):
        self.assertEqual(self.python('import os;os.kill(os.getppid(),0)')['status'],'error')
    def test_no_detached_descendants(self):
        self.assertEqual(self.python('import os;os.setsid()')['status'],'error')
    def test_file_limit(self):
        self.assertEqual(self.python("open('large','wb').write(b'x'*20*1024*1024)")['status'],'error')
