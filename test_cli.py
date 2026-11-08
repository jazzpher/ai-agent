"""Terminal agent (cli.py) tests: rendering, approvals, commands and the
workspace switch. No network, no real keys."""
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import cli
import agent as agent_mod
from approvals import gate
from test_agent_loop import FakeClient, Sbx, make_agent

HERE = os.path.dirname(os.path.abspath(__file__))
TICK = chr(96)


def plain_renderer():
    out = io.StringIO()
    return cli.Renderer(out, cli.Style(False)), out


def plan_marker(*steps):
    data = [{"text": t, "status": s} for t, s in steps]
    return "[[plan:" + base64.b64encode(json.dumps(data).encode()).decode() + "]]"


class CleanTest(unittest.TestCase):
    def test_strips_markers_and_transients(self):
        raw = ("Hi\n\n\u23f3 Waiting for the model\u2026\n\n"
               "[[planstep:2]]**Action:** " + TICK + "run_bash" + TICK +
               " [[detail:eyJhIjoxfQ==]]\n[[file:out/report.txt]]"
               '<div class="writing-live"><span>x</span><pre>draft</pre></div>'
               "<sub>model</sub>")
        text = cli.clean(raw)
        self.assertIn("Hi", text)
        self.assertIn("Action:", text)
        self.assertIn("run_bash", text)
        self.assertIn("+ out/report.txt", text)
        self.assertIn("model", text)
        for gone in ("[[", "\u23f3", "**", TICK, "<sub>", "writing-live", "draft"):
            self.assertNotIn(gone, text)

    def test_ask_marker_becomes_question(self):
        text = cli.clean("[[ask:Which one?|A|B]]")
        self.assertIn("? Which one?", text)
        self.assertIn("- A", text)
        self.assertIn("- B", text)

    def test_clean_is_idempotent(self):
        once = cli.clean("a **b** [[planstep:1]] c")
        self.assertEqual(cli.clean(once), once)

    def test_extract_plan(self):
        plan = cli.extract_plan("x " + plan_marker(("Read", "done"), ("Write", "running")))
        self.assertEqual(plan, [("Read", "done"), ("Write", "running")])
        self.assertIsNone(cli.extract_plan("no plan here"))


class RendererTest(unittest.TestCase):
    def test_prints_only_what_is_new(self):
        r, out = plain_renderer()
        r.feed("Hello")
        r.feed("Hello wor")
        r.feed("Hello world")
        self.assertEqual(out.getvalue(), "Hello world")

    def test_waits_for_a_marker_still_arriving(self):
        r, out = plain_renderer()
        r.feed("text [[detail:abc")
        self.assertEqual(out.getvalue(), "text ")
        r.feed("text [[detail:abc=]] more")
        self.assertEqual(out.getvalue(), "text  more")

    def test_rewritten_text_starts_a_new_line(self):
        r, out = plain_renderer()
        r.feed("abc")
        r.feed("abX")
        self.assertEqual(out.getvalue(), "abc\nX")

    def test_plan_printed_then_only_changed_steps(self):
        r, out = plain_renderer()
        r.feed(plan_marker(("Read files", "running"), ("Write code", "pending")))
        first = out.getvalue()
        self.assertIn("1. Read files", first)
        self.assertIn("2. Write code", first)
        r.feed(plan_marker(("Read files", "done"), ("Write code", "pending")) + "ok")
        rest = out.getvalue()[len(first):]
        self.assertIn("1. Read files", rest)
        self.assertNotIn("2. Write code", rest)
        self.assertTrue(rest.endswith("ok"))
        r.feed(plan_marker(("Read files", "done"), ("Write code", "pending")) + "ok!")
        self.assertTrue(out.getvalue().endswith("ok!"))


class EnvTest(unittest.TestCase):
    def test_parse_args(self):
        a = cli.parse_args(["-p", "hi", "-C", "x", "-m", "m1", "-y"])
        self.assertEqual((a.prompt, a.cwd, a.model, a.yes), ("hi", "x", "m1", True))
        a = cli.parse_args([])
        self.assertFalse(a.yes)
        self.assertIsNone(a.prompt)

    def test_prepare_env_points_workspace_at_folder(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}):
            os.environ.pop("AGENT_APPROVAL_TIMEOUT", None)
            ws = cli.prepare_env(tmp)
            self.assertEqual(ws, os.path.realpath(tmp))
            self.assertEqual(os.environ["AGENT_WORKSPACE"], os.path.realpath(tmp))
            self.assertEqual(os.environ["AGENT_APPROVAL_TIMEOUT"], cli.DEFAULT_APPROVAL_TIMEOUT)

    def _config_with(self, workspace):
        env = dict(os.environ)
        env.pop("AGENT_WORKSPACE", None)
        if workspace:
            env["AGENT_WORKSPACE"] = workspace
        code = ("import config, json; print(json.dumps([config.WORKSPACE_DIR, "
                "config.APP_WORKSPACE_DIR, config.MEMORY_FILE, config.FACTS_FILE]))")
        res = subprocess.run([sys.executable, "-c", code], cwd=HERE, env=env,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout.strip().splitlines()[-1])

    def test_config_follows_agent_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, app, memory, facts = self._config_with(tmp)
            self.assertEqual(ws, os.path.realpath(tmp))
            self.assertNotEqual(app, ws)
            self.assertEqual(os.path.dirname(memory), app)
            self.assertEqual(os.path.dirname(facts), app)

    def test_config_default_is_app_workspace(self):
        ws, app, memory, _ = self._config_with("")
        self.assertEqual(ws, app)
        self.assertEqual(os.path.dirname(memory), app)


class SessionTest(unittest.TestCase):
    def tearDown(self):
        patch.stopall()

    def session(self, client, answers="", auto_yes=False):
        a = make_agent(client)
        r, out = plain_renderer()
        s = cli.Session(a, r, auto_yes=auto_yes, stdin=io.StringIO(answers), out=out)
        return s, a, out

    def bash_setup(self):
        patch.object(agent_mod.session_manager, "get_or_create", return_value=Sbx("venv")).start()
        executed = []
        patch.dict(agent_mod.TOOL_FUNCTIONS,
                   {"run_bash": lambda **kw: executed.append(kw) or {"status": "success", "output": "ran"}}).start()
        patch.dict(os.environ, {"AGENT_APPROVAL": "auto"}).start()
        return executed

    def script(self):
        return FakeClient([("tool", "run_bash", {"command": "rm -rf ./build"}),
                           ("text", "all done")])

    def test_text_reply_streams_to_terminal(self):
        s, a, out = self.session(FakeClient([("text", "hello from the agent")]))
        last = s.turn("hi")
        self.assertIn("hello from the agent", out.getvalue())
        self.assertIn("hello from the agent", last)

    def test_yes_runs_the_command(self):
        executed = self.bash_setup()
        s, a, out = self.session(self.script(), answers="y\n")
        s.turn("clean the build")
        self.assertEqual(len(executed), 1)
        self.assertIn("approve?", out.getvalue())
        self.assertIn("all done", out.getvalue())
        self.assertIsNone(gate.pending(a.session_id))

    def test_no_denies_and_tells_the_model(self):
        executed = self.bash_setup()
        s, a, out = self.session(self.script(), answers="n\n")
        s.turn("clean the build")
        self.assertEqual(executed, [])
        self.assertIn("denied", out.getvalue())
        tool_msgs = [m for m in a.messages if m.get("role") == "tool"]
        self.assertIn("denied", tool_msgs[-1]["content"])

    def test_always_turns_on_auto_yes(self):
        executed = self.bash_setup()
        s, a, out = self.session(self.script(), answers="a\n")
        s.turn("clean the build")
        self.assertEqual(len(executed), 1)
        self.assertTrue(s.auto_yes)

    def test_auto_yes_never_asks(self):
        executed = self.bash_setup()
        s, a, out = self.session(self.script(), auto_yes=True)
        s.turn("clean the build")
        self.assertEqual(len(executed), 1)
        self.assertNotIn("approve?", out.getvalue())


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        fake = SimpleNamespace(model="m1", session_id="s",
                               _providers=[{"model": "m1", "preset": "groq"},
                                           {"model": "m2", "preset": "nvidia"}])
        fake.use_model = lambda m: self.calls.append(m) or setattr(fake, "model", m) or m == "m2"
        fake.reset = lambda: self.calls.append("reset")
        r, self.out = plain_renderer()
        self.s = cli.Session(fake, r, stdin=io.StringIO(""), out=self.out)

    def test_commands(self):
        self.assertTrue(self.s.command("/model m2"))
        self.assertIn("model: m2", self.out.getvalue())
        self.s.command("/model other")
        self.assertIn("not in providers.json", self.out.getvalue())
        self.s.command("/models")
        self.assertIn("m1  (groq)", self.out.getvalue())
        self.s.command("/yes")
        self.assertTrue(self.s.auto_yes)
        self.s.command("/clear")
        self.assertIn("reset", self.calls)
        self.s.command("/nope")
        self.assertIn("unknown command", self.out.getvalue())
        self.assertFalse(self.s.command("/exit"))


class MainTest(unittest.TestCase):
    def tearDown(self):
        patch.stopall()

    def test_one_shot_prompt(self):
        a = make_agent(FakeClient([("text", "one shot answer")]))
        patch.object(cli, "build_agent", return_value=a).start()
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}), redirect_stdout(buf):
            code = cli.main(["-p", "hi", "-C", tmp])
        self.assertEqual(code, 0)
        self.assertIn("one shot answer", buf.getvalue())

    def test_missing_folder(self):
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.main(["-C", os.path.join(HERE, "no-such-folder-xyz")])
        self.assertEqual(code, 2)
        self.assertIn("no such folder", err.getvalue())


if __name__ == "__main__":
    unittest.main()
