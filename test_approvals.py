import os
import threading
import time
import unittest
from unittest.mock import patch

import approvals
from approvals import ApprovalGate, needs_approval


class NeedsApprovalTest(unittest.TestCase):
    def test_risky_without_docker_asks(self):
        with patch.dict(os.environ, {"AGENT_APPROVAL": "auto"}):
            self.assertTrue(needs_approval("run_bash", "risky", "venv"))
            self.assertFalse(needs_approval("run_bash", "risky", "docker"))
            self.assertFalse(needs_approval("run_bash", "safe", "venv"))

    def test_pip_install_always_asks_without_docker(self):
        with patch.dict(os.environ, {"AGENT_APPROVAL": "auto"}):
            self.assertTrue(needs_approval("pip_install", "safe", "venv"))
            self.assertFalse(needs_approval("pip_install", "safe", "docker"))

    def test_other_tools_never_ask(self):
        with patch.dict(os.environ, {"AGENT_APPROVAL": "on"}):
            self.assertFalse(needs_approval("read_file", "risky", "venv"))

    def test_modes(self):
        with patch.dict(os.environ, {"AGENT_APPROVAL": "off"}):
            self.assertFalse(needs_approval("run_bash", "risky", "venv"))
        with patch.dict(os.environ, {"AGENT_APPROVAL": "on"}):
            self.assertTrue(needs_approval("run_bash", "risky", "docker"))
        with patch.dict(os.environ, {"AGENT_APPROVAL": "nonsense"}):
            self.assertEqual(approvals.approval_mode(), "auto")


class GateTest(unittest.TestCase):
    def ask_in_thread(self, gate, **kw):
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("r", gate.request("s1", "rm x", **kw)))
        t.start()
        for _ in range(50):
            if gate.pending("s1"):
                break
            time.sleep(0.02)
        return t, out

    def test_approve(self):
        g = ApprovalGate()
        t, out = self.ask_in_thread(g, timeout=5)
        self.assertEqual(g.pending("s1"), "rm x")
        self.assertTrue(g.answer("s1", True))
        t.join()
        self.assertTrue(out["r"])
        self.assertIsNone(g.pending("s1"))

    def test_deny(self):
        g = ApprovalGate()
        t, out = self.ask_in_thread(g, timeout=5)
        g.answer("s1", False)
        t.join()
        self.assertFalse(out["r"])

    def test_timeout_denies(self):
        g = ApprovalGate()
        self.assertFalse(g.request("s1", "rm x", timeout=0.3))
        self.assertIsNone(g.pending("s1"))

    def test_cancel_denies(self):
        g = ApprovalGate()
        self.assertFalse(g.request("s1", "rm x", timeout=5, cancelled=lambda: True))

    def test_answer_without_pending(self):
        self.assertFalse(ApprovalGate().answer("nope", True))

    def test_sessions_are_separate(self):
        g = ApprovalGate()
        t, out = self.ask_in_thread(g, timeout=5)
        self.assertFalse(g.answer("other", True))
        g.answer("s1", True)
        t.join()


class AgentIntegrationTest(unittest.TestCase):
    def make_agent(self):
        import agent as agent_mod
        a = agent_mod.AIAgent(api_key="fake-key")
        return agent_mod, a

    def test_prompt_for_risky_command_without_docker(self):
        agent_mod, a = self.make_agent()
        class Sbx: mode = "venv"
        with patch.dict(os.environ, {"AGENT_APPROVAL": "auto"}), \
             patch.object(agent_mod.session_manager, "get_or_create", return_value=Sbx()):
            risky = a._approval_prompt("run_bash", {"command": "rm -rf ./build"})
            safe = a._approval_prompt("run_bash", {"command": "ls"})
            blocked = a._approval_prompt("run_bash", {"command": "format C:"})
            pip = a._approval_prompt("pip_install", {"package": "rich"})
        self.assertIsNotNone(risky)
        self.assertIn("rm -rf", risky[0])
        self.assertIsNone(safe)
        self.assertIsNone(blocked)
        self.assertIsNotNone(pip)

    def test_no_prompt_with_docker(self):
        agent_mod, a = self.make_agent()
        class Sbx: mode = "docker"
        with patch.dict(os.environ, {"AGENT_APPROVAL": "auto"}), \
             patch.object(agent_mod.session_manager, "get_or_create", return_value=Sbx()):
            self.assertIsNone(a._approval_prompt("run_bash", {"command": "rm -rf ./build"}))


if __name__ == "__main__":
    unittest.main()
