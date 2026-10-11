"""Host (venv) mode must not leak secrets or read outside the workspace."""
import os
import tempfile
import unittest
from unittest.mock import patch

import sandbox_session
from safety import SafetyGuard


class HostEnvTest(unittest.TestCase):
    def test_secrets_are_stripped(self):
        fake = {"PATH": "/usr/bin", "NVIDIA_API_KEY": "nv", "GEMINI_API_KEY": "g",
                "OPENROUTER_API_KEY": "o", "GITHUB_TOKEN": "t", "AGENT_PASSWORD": "p",
                "AGENT_USERNAME": "u", "MY_SECRET": "s", "HOME": "/home/x", "LANG": "C"}
        with patch.dict(os.environ, fake, clear=True):
            env = sandbox_session.host_env("/v", "/v/bin/python")
        for k in ("NVIDIA_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "GITHUB_TOKEN",
                  "AGENT_PASSWORD", "AGENT_USERNAME", "MY_SECRET"):
            self.assertNotIn(k, env)
        self.assertEqual(env["HOME"], "/home/x")
        self.assertTrue(env["PATH"].startswith("/v/bin"))
        self.assertEqual(env["VIRTUAL_ENV"], "/v")

    def test_host_exec_child_cannot_see_keys(self):
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "leak-me"}):
            sbx = sandbox_session.SessionSandbox.__new__(sandbox_session.SessionSandbox)
            sbx._venv_path = os.path.dirname(os.path.dirname(os.__file__))
            sbx._python_path = os.sys.executable
            out = sbx._host_python("import os;print(os.environ.get('NVIDIA_API_KEY','none'))", 20)
        self.assertIn("none", out["output"])
        self.assertNotIn("leak-me", out["output"])


class ReadScopeTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.outside = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        self.outside.write("private"); self.outside.close()
        self.guard = SafetyGuard(self.ws)

    def test_outside_read_blocked_by_default(self):
        with patch.dict(os.environ, {"AGENT_READ_OUTSIDE_WORKSPACE": ""}):
            self.assertFalse(self.guard.validate_file_read(self.outside.name)["safe"])
            inside = os.path.join(self.ws, "a.txt")
            open(inside, "w").close()
            self.assertTrue(self.guard.validate_file_read(inside)["safe"])

    def test_opt_in_allows_outside_reads(self):
        with patch.dict(os.environ, {"AGENT_READ_OUTSIDE_WORKSPACE": "1"}):
            self.assertTrue(self.guard.validate_file_read(self.outside.name)["safe"])


if __name__ == "__main__":
    unittest.main()
