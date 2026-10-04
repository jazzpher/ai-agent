"""Plan parsing, memory tool, plan-aware self-check, and beat-wrapped slow UI actions. No network."""
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import agentic

ANALYSIS = """**Understanding:** x
**Goal:** y

**Plan:**
1. Search the web for sources
2. Fetch the top pages
- Write the summary

**Success criteria:** cites urls"""


class PlanTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(agentic.parse_plan(ANALYSIS),
                         ["Search the web for sources", "Fetch the top pages", "Write the summary"])

    def test_parse_empty_and_cap(self):
        self.assertEqual(agentic.parse_plan(""), [])
        self.assertEqual(agentic.parse_plan("no plan here"), [])
        many = "**Plan:**\n" + "\n".join(f"{i}. s{i}" for i in range(1, 12))
        self.assertEqual(len(agentic.parse_plan(many)), agentic.MAX_PLAN_STEPS)


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "MEMORY.md")

    def test_add_list_forget(self):
        self.assertEqual(agentic.memory_action(self.path, "add", "Prefers Taglish")["status"], "success")
        self.assertIn("already", agentic.memory_action(self.path, "add", "prefers taglish")["output"].lower())
        self.assertIn("Prefers Taglish", agentic.memory_action(self.path, "list")["output"])
        self.assertIn("Forgot 1", agentic.memory_action(self.path, "forget", "taglish")["output"])
        self.assertIn("empty", agentic.memory_action(self.path, "list")["output"])

    def test_refuses_secrets(self):
        r = agentic.memory_action(self.path, "add", "my key is nvapi-abcdefghijkl123")
        self.assertEqual(r["status"], "error")
        r = agentic.memory_action(self.path, "add", "password: hunter2hunter2")
        self.assertEqual(r["status"], "error")
        self.assertFalse(os.path.exists(self.path))

    def test_cap_and_bad_action(self):
        for i in range(agentic.MAX_MEMORY_ITEMS):
            agentic.memory_action(self.path, "add", f"fact {i}")
        self.assertEqual(agentic.memory_action(self.path, "add", "one more")["status"], "error")
        self.assertEqual(agentic.memory_action(self.path, "zap")["status"], "error")

    def test_tool_registered_and_loaded_into_prompt(self):
        import tools, agent as agent_mod
        self.assertIn("memory", tools.TOOL_FUNCTIONS)
        self.assertIn("memory", [d["function"]["name"] for d in tools.TOOL_DEFINITIONS])
        with patch.object(agent_mod, "MEMORY_FILE", self.path):
            agentic.memory_action(self.path, "add", "Owner likes short answers")
            ag = agent_mod.AIAgent(api_key="k")
            self.assertIn("Owner likes short answers", ag.system_prompt)


class DispatchTest(unittest.TestCase):
    def test_memory_tool_accepts_session_id_like_other_tools(self):
        import tools
        with patch("config.MEMORY_FILE", os.path.join(tempfile.mkdtemp(), "M.md")):
            r = tools.TOOL_FUNCTIONS["memory"](action="list", session_id="abc")
        self.assertEqual(r["status"], "success")


class VerifyUsesPlanTest(unittest.TestCase):
    def test_plan_in_verify_prompt(self):
        import agent as agent_mod
        ag = agent_mod.AIAgent(api_key="k")
        ag._plan_steps = ["Do A", "Do B"]
        ag._turn_evidence = [("run_python", "success", "ok")]
        seen = {}

        def fake(client, **kw):
            seen["msgs"] = kw["messages"]
            class R:  # minimal response
                choices = [type("C", (), {"message": type("M", (), {"content": "PASS"})()})()]
            return R()
        with patch.object(agent_mod, "completion_with_retry", fake):
            self.assertEqual(ag._verify_final(None, "goal", "answer"), "PASS")
        self.assertIn("1. Do A", seen["msgs"][1]["content"])
        self.assertIn("2. Do B", seen["msgs"][1]["content"])


class BeatsTest(unittest.TestCase):
    def test_slow_call_keeps_streaming_then_result(self):
        try:
            import app
        except Exception as e:
            self.skipTest(f"app import failed: {e}")
        out = list(app.run_with_beats(lambda: (time.sleep(0.5), "RESULT")[1], "Testing", interval=0.1))
        self.assertGreaterEqual(len(out), 4)  # start + several beats + result
        self.assertTrue(out[1].startswith("⏳ Testing"))
        self.assertEqual(out[-1], "RESULT")

    def test_error_is_a_line_not_a_crash(self):
        try:
            import app
        except Exception as e:
            self.skipTest(f"app import failed: {e}")
        def boom():
            raise RuntimeError("nope")
        out = list(app.run_with_beats(boom, "Testing", interval=0.1))
        self.assertIn("RuntimeError", out[-1])


class ProviderButtonsStreamTest(unittest.TestCase):
    def test_test_fetch_benchmark_handlers_are_generators(self):
        try:
            import app
        except Exception as e:
            self.skipTest(f"app import failed: {e}")
        import inspect
        blocks = app.build_app()
        gens = [f for f in blocks.fns.values() if inspect.isgeneratorfunction(f.fn)]
        names = {f.fn.__name__ for f in gens}
        self.assertTrue({"test_h", "fetch_h", "bench_h"} <= names, names)


if __name__ == "__main__":
    unittest.main()
