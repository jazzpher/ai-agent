import os
import threading
import time
import unittest
from unittest.mock import patch

import approvals
from approvals import ApprovalGate, needs_approval


class NeedsApprovalTest(unittest.TestCase):
    def test_everything_asks_on_unisolated_host(self):
        with patch.dict(os.environ, {"AGENT_APPROVAL": "auto"}):
            self.assertTrue(needs_approval("run_bash", "risky", "venv"))
            self.assertTrue(needs_approval("run_bash", "safe", "venv"))
            self.assertTrue(needs_approval("run_python", "safe", "venv"))
            self.assertFalse(needs_approval("run_bash", "risky", "docker"))
            self.assertFalse(needs_approval("run_bash", "safe", "bubblewrap"))

    def test_risky_mode_keeps_old_behaviour(self):
        with patch.dict(os.environ, {"AGENT_APPROVAL": "risky"}):
            self.assertTrue(needs_approval("run_bash", "risky", "venv"))
            self.assertFalse(needs_approval("run_bash", "safe", "venv"))
            self.assertTrue(needs_approval("pip_install", "safe", "venv"))

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
        self.assertIsNone(safe)             # read-only allowlist: `ls` runs without a prompt
        self.assertIsNone(blocked)
        self.assertIsNotNone(pip)

    def test_no_prompt_with_docker(self):
        agent_mod, a = self.make_agent()
        class Sbx: mode = "docker"
        with patch.dict(os.environ, {"AGENT_APPROVAL": "auto"}), \
             patch.object(agent_mod.session_manager, "get_or_create", return_value=Sbx()):
            self.assertIsNone(a._approval_prompt("run_bash", {"command": "rm -rf ./build"}))


class SafeReadonlyTest(unittest.TestCase):
    def test_allowlist_rules(self):
        from approvals import safe_readonly
        for cmd in ("ls", "ls -la", "ls -la src/app", "pwd", "git status", "git log --oneline",
                    "git log -n5 --stat", "git diff --cached", "python --version", "pip list",
                    "pip show gradio"):
            self.assertTrue(safe_readonly(cmd), cmd)

    def test_shell_tricks_and_unsafe_forms_are_refused(self):
        from approvals import safe_readonly
        bad = ["", "ls; rm -rf /", "ls && del x", "cat x > y", "echo `id`", "ls | wc", "git push",
               "del /s C:\\", "rm -rf build", "$(id)",
               "echo $(touch /tmp/pwned)", "ls\ntouch /tmp/x", "ls\r\ntouch x", "echo ${HOME}",
               "ls $(id)", "ls (id)", "ls {a,b}", "ls 'x'", 'ls "x"', "ls ~", "ls *",
               "cat .env", "cat ~/.ssh/id_rsa", "head -n1 /etc/passwd", "tail -f x",
               "type secret.txt", "echo hi", "file -C", "which python", "wc -l x",
               "git diff --output=/tmp/o", "git log --output=x", "git log --outp=x",
               "git diff --no-index /etc/passwd x", "git diff --ext-diff", "git -c core.pager=x log",
               "git branch -D main", "git branch", "git status --ignored -u",
               "hostname evil", "date -s 2020-01-01", "python -c 1", "pip install x", "pip show -f x",
               "ls -la --color=always", "whoami root"]
        for cmd in bad:
            self.assertFalse(safe_readonly(cmd), repr(cmd))

    def test_needs_approval_skips_only_safe_bash(self):
        from approvals import needs_approval
        self.assertFalse(needs_approval("run_bash", "safe", "venv", command="git status"))
        self.assertTrue(needs_approval("run_bash", "safe", "venv", command="rm -rf x"))
        self.assertTrue(needs_approval("run_bash", "risky", "venv", command="ls"))
        self.assertTrue(needs_approval("pip_install", "risky", "venv"))
        self.assertFalse(needs_approval("run_bash", "safe", "docker", command="rm -rf x"))


if __name__ == "__main__":
    unittest.main()
