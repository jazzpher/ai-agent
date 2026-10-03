"""Agent loop tests with a scripted fake client. No network, no real keys."""
import json
import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import agent as agent_mod
from approvals import gate


def stream_chunks(item):
    if item[0] == "text":
        delta = SimpleNamespace(content=item[1], tool_calls=None)
    else:
        tc = SimpleNamespace(index=0, id="call1",
                             function=SimpleNamespace(name=item[1], arguments=json.dumps(item[2])))
        delta = SimpleNamespace(content=None, tool_calls=[tc])
    return iter([SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason="stop")])])


class FakeClient:
    """stream=True calls pop from `script`; stream=False calls pop from `sync`."""
    def __init__(self, script, sync=None):
        self.script = list(script)
        self.sync = list(sync or [])
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        if kw.get("stream"):
            return stream_chunks(self.script.pop(0))
        text = self.sync.pop(0) if self.sync else "KEEP"
        msg = SimpleNamespace(content=text)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)


def make_agent(client, vision=False):
    a = agent_mod.AIAgent(api_key="fake-key")
    a.vision = vision
    patcher = patch.object(a, "_get_client", return_value=client)
    patcher.start()
    patch.object(a, "_should_analyze", return_value=False).start()
    patch.object(a, "_log", lambda *args, **kw: None).start()
    return a


def run(a, message="test"):
    out = ""
    for out in a.chat_stream(message):
        pass
    return out


class Sbx:
    def __init__(self, mode): self.mode = mode


class ApprovalLoopTest(unittest.TestCase):
    def tearDown(self):
        patch.stopall()

    def drive(self, answer):
        client = FakeClient([("tool", "run_bash", {"command": "rm -rf ./build"}),
                             ("text", "done")])
        a = make_agent(client)
        patch.object(agent_mod.session_manager, "get_or_create", return_value=Sbx("venv")).start()
        executed = []
        patch.dict(agent_mod.TOOL_FUNCTIONS,
                   {"run_bash": lambda **kw: executed.append(kw) or {"status": "success", "output": "ran"}}).start()

        def responder():
            for _ in range(100):
                if gate.pending(a.session_id):
                    gate.answer(a.session_id, answer)
                    return
                time.sleep(0.05)
        t = threading.Thread(target=responder)
        t.start()
        with patch.dict(os.environ, {"AGENT_APPROVAL": "auto"}):
            out = run(a)
        t.join()
        return out, executed, a

    def test_approved_command_runs(self):
        out, executed, _ = self.drive(True)
        self.assertEqual(len(executed), 1)
        self.assertIn("approval", out.lower())

    def test_denied_command_does_not_run_and_model_is_told(self):
        out, executed, a = self.drive(False)
        self.assertEqual(executed, [])
        tool_msgs = [m for m in a.messages if m.get("role") == "tool"]
        self.assertIn("denied", tool_msgs[-1]["content"])


class VerifyLoopTest(unittest.TestCase):
    def tearDown(self):
        patch.stopall()

    def setup_tool(self):
        patch.dict(agent_mod.TOOL_FUNCTIONS,
                   {"run_bash": lambda **kw: {"status": "error", "output": "no such file"}}).start()
        patch.object(agent_mod.session_manager, "get_or_create", return_value=Sbx("docker")).start()

    def test_failed_verification_sends_model_back_once(self):
        self.setup_tool()
        client = FakeClient(
            [("tool", "run_bash", {"command": "ls"}), ("text", "all done!"),
             ("text", "it failed: no such file")],
            sync=["KEEP", "FAIL\nIssue: ignored error\nFix: report the error", "PASS"])
        a = make_agent(client)
        out = run(a)
        self.assertIn("May kulang", out)
        feedback = [m for m in a.messages if m.get("role") == "user" and "Verification feedback" in str(m.get("content"))]
        self.assertEqual(len(feedback), 1)
        self.assertEqual(a.messages[-1]["content"], "it failed: no such file")

    def test_pass_ends_turn(self):
        self.setup_tool()
        client = FakeClient([("tool", "run_bash", {"command": "ls"}), ("text", "done")],
                            sync=["KEEP", "PASS"])
        out = run(make_agent(client))
        self.assertIn("Na-check", out)
        self.assertEqual(client.script, [])

    def test_no_check_without_tool_use(self):
        client = FakeClient([("text", "hello")])
        out = run(make_agent(client))
        self.assertNotIn("Checking", out)
        self.assertEqual(len([c for c in client.calls if not c.get("stream")]), 0)

    def test_rounds_are_capped(self):
        self.setup_tool()
        client = FakeClient(
            [("tool", "run_bash", {"command": "ls"}), ("text", "a"), ("text", "b"), ("text", "c")],
            sync=["KEEP", "FAIL\nIssue: x\nFix: y", "FAIL\nIssue: x\nFix: y", "FAIL\nIssue: x\nFix: y"])
        run(make_agent(client))
        verify_calls = [c for c in client.calls if not c.get("stream")
                        and "check an AI agent" in c["messages"][0]["content"]]
        self.assertEqual(len(verify_calls), agent_mod.MAX_VERIFY_ROUNDS)

    def test_can_be_turned_off(self):
        self.setup_tool()
        client = FakeClient([("tool", "run_bash", {"command": "ls"}), ("text", "done")], sync=["KEEP"])
        with patch.dict(os.environ, {"AGENT_VERIFY": "off"}):
            out = run(make_agent(client))
        self.assertNotIn("Checking", out)

    def test_verifier_error_never_blocks(self):
        self.setup_tool()
        client = FakeClient([("tool", "run_bash", {"command": "ls"}), ("text", "done")], sync=["KEEP"])
        a = make_agent(client)
        with patch.object(a, "_verify_final", wraps=a._verify_final):
            real = client.create
            def flaky(**kw):
                if not kw.get("stream") and "check an AI agent" in kw["messages"][0]["content"]:
                    raise RuntimeError("boom")
                return real(**kw)
            client.chat.completions.create = flaky
            out = run(a)
        self.assertIn("Na-check", out)


class VisionTest(unittest.TestCase):
    def tearDown(self):
        patch.stopall()

    def image_result(self, tmp):
        from PIL import Image
        path = os.path.join(tmp, "pic.png")
        Image.new("RGB", (40, 30), "red").save(path)
        return {"status": "success", "output": "Image: pic.png", "file_type": "image", "image_path": path}

    def drive(self, vision):
        import tempfile
        tmp = tempfile.mkdtemp()
        res = self.image_result(tmp)
        patch.dict(agent_mod.TOOL_FUNCTIONS, {"view_file": lambda **kw: dict(res)}).start()
        client = FakeClient([("tool", "view_file", {"path": "pic.png"}), ("text", "ok")])
        a = make_agent(client, vision=vision)
        # make_agent sets vision, but refresh_providers is not called by chat_stream
        run(a)
        return a, client

    def test_vision_on_attaches_image_after_tool_message(self):
        a, client = self.drive(True)
        idx_tool = max(i for i, m in enumerate(a.messages) if m.get("role") == "tool")
        nxt = a.messages[idx_tool + 1]
        self.assertEqual(nxt["role"], "user")
        self.assertEqual(nxt["content"][1]["type"], "image_url")
        self.assertTrue(nxt["content"][1]["image_url"]["url"].startswith("data:image/"))

    def test_vision_off_sends_no_image_and_warns_model(self):
        a, client = self.drive(False)
        self.assertFalse(any(isinstance(m.get("content"), list) for m in a.messages))
        tool_msg = [m for m in a.messages if m.get("role") == "tool"][-1]["content"]
        self.assertIn("vision turned off", tool_msg)

    def test_only_newest_image_kept(self):
        from vision import strip_old_images
        img = lambda: {"role": "user", "content": [{"type": "text", "text": "Image(s)"},
                                                   {"type": "image_url", "image_url": {"url": "data:x"}}]}
        msgs = [img(), {"role": "assistant", "content": "a"}, img()]
        strip_old_images(msgs)
        self.assertIsInstance(msgs[0]["content"], str)
        self.assertIsInstance(msgs[2]["content"], list)

    def test_apply_provider_reads_vision_flag(self):
        a = agent_mod.AIAgent(api_key="fake-key")
        a._apply_provider({"base_url": "u", "api_key": "k", "model": "m", "vision": True})
        self.assertTrue(a.vision)
        a._apply_provider({"base_url": "u", "api_key": "k", "model": "m"})
        self.assertFalse(a.vision)


if __name__ == "__main__":
    unittest.main()


class NoLimitTest(unittest.TestCase):
    def tearDown(self):
        patch.stopall()

    def setup_tool(self):
        self.executed = []
        patch.dict(agent_mod.TOOL_FUNCTIONS,
                   {"run_bash": lambda **kw: self.executed.append(kw) or {"status": "success", "output": "ok"}}).start()
        patch.object(agent_mod.session_manager, "get_or_create", return_value=Sbx("docker")).start()

    def test_no_default_limits(self):
        import config
        self.assertIsNone(config.MAX_ITERATIONS)
        self.assertIsNone(config.MAX_TOTAL_SECONDS)

    def test_runs_past_old_20_step_limit(self):
        self.setup_tool()
        script = [("tool", "run_bash", {"command": f"echo {i}"}) for i in range(30)] + [("text", "finished")]
        a = make_agent(FakeClient(script))
        with patch.dict(os.environ, {"AGENT_VERIFY": "off"}):
            out = run(a)
        self.assertEqual(len(self.executed), 30)
        self.assertIn("finished", out)
        self.assertNotIn("Stopped", out)

    def test_no_wall_clock_cutoff(self):
        self.setup_tool()
        a = make_agent(FakeClient([("tool", "run_bash", {"command": "ls"}), ("text", "done")]))
        real = time.time
        with patch.object(agent_mod.time, "time", side_effect=lambda: real() + 10_000), \
                patch.dict(os.environ, {"AGENT_VERIFY": "off"}):
            out = run(a)
        self.assertNotIn("budget", out)
        self.assertIn("done", out)

    def test_identical_repeated_calls_are_halted(self):
        self.setup_tool()
        script = [("tool", "run_bash", {"command": "ls"})] * 10
        a = make_agent(FakeClient(script))
        out = run(a)
        self.assertEqual(len(self.executed), agent_mod.REPEAT_CALL_LIMIT - 1)
        self.assertIn("looks like a loop", out)
        # every tool_call has a reply, so the next turn is valid
        calls = sum(len(m.get("tool_calls") or []) for m in a.messages if m.get("role") == "assistant")
        replies = len([m for m in a.messages if m.get("role") == "tool"])
        self.assertEqual(calls, replies)

    def test_different_calls_do_not_trigger_guard(self):
        self.setup_tool()
        script = [("tool", "run_bash", {"command": "ls"}), ("tool", "run_bash", {"command": "pwd"})] * 6 + [("text", "ok")]
        with patch.dict(os.environ, {"AGENT_VERIFY": "off"}):
            out = run(make_agent(FakeClient(script)))
        self.assertEqual(len(self.executed), 12)
        self.assertNotIn("loop", out)

    def test_stop_button_still_halts(self):
        self.setup_tool()
        a = make_agent(FakeClient([("tool", "run_bash", {"command": "ls"})] * 3))
        a.cancel_requested = False
        orig = a._get_client()
        gen = a.chat_stream("go")
        next(gen)
        a.cancel_requested = True
        out = ""
        for out in gen:
            pass
        self.assertIn("cancel", out.lower())


class HeartbeatTest(unittest.TestCase):
    def tearDown(self):
        patch.stopall()

    def test_slow_tool_keeps_stream_alive(self):
        patch.object(agent_mod.session_manager, "get_or_create", return_value=Sbx("docker")).start()
        patch.object(agent_mod, "HEARTBEAT_SECONDS", 0.05).start()
        patch.dict(agent_mod.TOOL_FUNCTIONS,
                   {"run_bash": lambda **kw: time.sleep(0.4) or {"status": "success", "output": "ok"}}).start()
        a = make_agent(FakeClient([("tool", "run_bash", {"command": "slow"}), ("text", "done")]))
        outs = []
        with patch.dict(os.environ, {"AGENT_VERIFY": "off"}):
            for o in a.chat_stream("go"):
                outs.append(o)
        beats = [o for o in outs if "Tumatakbo ang run_bash" in o]
        self.assertGreaterEqual(len(beats), 3)
        self.assertIn("done", outs[-1])
        # heartbeat text is transient, not kept in the final message
        self.assertNotIn("Tumatakbo ang run_bash...", outs[-1])

    def test_slow_sandbox_creation_keeps_stream_alive(self):
        def slow_create(*a, **k):
            time.sleep(0.4)
            return Sbx("docker")
        patch.object(agent_mod.session_manager, "get_or_create", side_effect=slow_create).start()
        patch.object(agent_mod, "HEARTBEAT_SECONDS", 0.05).start()
        patch.dict(agent_mod.TOOL_FUNCTIONS,
                   {"run_bash": lambda **kw: {"status": "success", "output": "ok"}}).start()
        a = make_agent(FakeClient([("tool", "run_bash", {"command": "ls"}), ("text", "done")]))
        outs = []
        with patch.dict(os.environ, {"AGENT_VERIFY": "off"}):
            for o in a.chat_stream("go"):
                outs.append(o)
        self.assertGreaterEqual(len([o for o in outs if "Inihahanda ang sandbox" in o]), 3)

    def test_tool_exception_still_reported(self):
        def boom(**kw): raise ValueError("x")
        patch.object(agent_mod.session_manager, "get_or_create", return_value=Sbx("docker")).start()
        patch.dict(agent_mod.TOOL_FUNCTIONS, {"run_bash": boom}).start()
        a = make_agent(FakeClient([("tool", "run_bash", {"command": "ls"}), ("text", "done")]))
        with patch.dict(os.environ, {"AGENT_VERIFY": "off"}):
            out = run(a)
        self.assertIn("done", out)


class SandboxSetupTest(unittest.TestCase):
    def test_missing_core_packages_only_lists_unimportable(self):
        import sandbox_session as ss
        with patch("importlib.util.find_spec", side_effect=lambda m: None if m == "pandas" else object()):
            self.assertEqual(ss.missing_core_packages(), ["pandas"])

    def test_venv_reuses_host_packages_and_skips_pip_upgrade(self):
        import sandbox_session as ss
        calls = []
        with patch.object(ss.subprocess, "run", side_effect=lambda cmd, **k: calls.append(cmd) or SimpleNamespace(returncode=0, stdout="", stderr="")), \
                patch.object(ss, "missing_core_packages", return_value=[]):
            ss.SessionSandbox("t1", force_mode="venv")
        self.assertEqual(len(calls), 1)
        self.assertIn("--system-site-packages", calls[0])
        self.assertFalse(any("--upgrade" in c for c in calls))
