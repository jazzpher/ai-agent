"""Manager + parallel workers with scripted fake clients. No network."""
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import workers
import tools
from test_agent_loop import FakeClient, make_agent, run


def tc(i, name, args):
    return SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def reply(content=None, calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))])


class Scripted:
    def __init__(self, scripts, delay=0.0):
        self.scripts, self.delay, self.lock = scripts, delay, threading.Lock()
        self.active = self.peak = 0

    def __call__(self, client, **kw):
        key = kw["messages"][1]["content"]
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(self.delay)
        with self.lock:
            self.active -= 1
            step = self.scripts[key].pop(0)
        if isinstance(step, Exception):
            raise step
        return step


EXEC = lambda name, args: {"status": "success", "output": f"{name}:{args}"}
DEFS = tools.TOOL_DEFINITIONS


class WorkerTest(unittest.TestCase):
    def test_parallel_and_ordered(self):
        s = Scripted({"A": [reply("res A")], "B": [reply("res B")], "C": [reply("res C")]}, delay=0.3)
        t0 = time.monotonic()
        r = workers.run_workers([{"title": k, "instructions": k} for k in "ABC"], None, "m", DEFS, EXEC, create=s)
        self.assertLess(time.monotonic() - t0, 0.8)
        self.assertEqual(s.peak, 3)
        self.assertEqual([w["output"] for w in r["workers"]], ["res A", "res B", "res C"])

    def test_worker_uses_tools_then_answers(self):
        s = Scripted({"A": [reply(None, [tc(1, "web_search", {"query": "x"})]), reply("found it")]})
        seen = []
        r = workers.run_workers([{"title": "A", "instructions": "A"}], None, "m", DEFS,
                                lambda n, a: seen.append((n, a)) or {"status": "success", "output": "ok"}, create=s)
        self.assertEqual(seen, [("web_search", {"query": "x"})])
        self.assertEqual(r["workers"][0]["output"], "found it")

    def test_one_failure_does_not_sink_the_others(self):
        s = Scripted({"A": [RuntimeError("boom")], "B": [reply("fine")]})
        r = workers.run_workers([{"title": "A", "instructions": "A"}, {"title": "B", "instructions": "B"}],
                                None, "m", DEFS, EXEC, create=s)
        self.assertEqual([w["status"] for w in r["workers"]], ["error", "success"])

    def test_write_tools_blocked_for_workers(self):
        s = Scripted({"A": [reply(None, [tc(1, "run_bash", {"command": "rm -rf /"})]), reply("ok")]})
        ran = []
        r = workers.run_workers([{"title": "A", "instructions": "A"}], None, "m", DEFS,
                                lambda n, a: ran.append(n) or {}, create=s)
        self.assertEqual(ran, [])
        self.assertEqual(r["workers"][0]["status"], "success")

    def test_repeat_guard_timeout_cancel(self):
        call = tc(1, "web_search", {"query": "same"})
        s = Scripted({"A": [reply(None, [call]) for _ in range(10)]})
        r = workers.run_workers([{"title": "A", "instructions": "A"}], None, "m", DEFS, EXEC, create=s)
        self.assertIn("5 times", r["workers"][0]["output"])
        r2 = workers.run_workers([{"title": "A", "instructions": "A"}], None, "m", DEFS, EXEC, seconds=-1,
                                 create=Scripted({"A": [reply("x")]}))
        self.assertEqual(r2["workers"][0]["status"], "timeout")
        r3 = workers.run_workers([{"title": "A", "instructions": "A"}], None, "m", DEFS, EXEC,
                                 cancelled=lambda: True, create=Scripted({"A": []}))
        self.assertEqual(r3["workers"][0]["status"], "cancelled")

    def test_bad_input_and_cap(self):
        self.assertEqual(workers.run_workers(None, None, "m", DEFS, EXEC)["status"], "error")
        self.assertEqual(workers.run_workers([], None, "m", DEFS, EXEC)["status"], "error")
        r = workers.run_workers(["A"], None, "m", DEFS, EXEC, create=Scripted({"A": [reply("   ")]}))
        self.assertEqual(r["workers"][0]["status"], "error")
        many = [{"title": str(i), "instructions": str(i)} for i in range(9)]
        s = Scripted({str(i): [reply("x")] for i in range(9)})
        self.assertEqual(len(workers.run_workers(many, None, "m", DEFS, EXEC, create=s)["workers"]), workers.MAX_TASKS)


class ManagerWiringTest(unittest.TestCase):
    def tearDown(self):
        patch.stopall()

    def test_tool_registered(self):
        self.assertIn("delegate_tasks", tools.TOOL_FUNCTIONS)
        self.assertIn("delegate_tasks", [d["function"]["name"] for d in DEFS])

    def test_real_workers_through_agent(self):
        client = FakeClient([("tool", "delegate_tasks", {"tasks": [
            {"title": "a", "instructions": "do a"}, {"title": "b", "instructions": "do b"}]}),
            ("text", "final")])
        a = make_agent(client)
        s = Scripted({"do a": [reply("A done")], "do b": [reply("B done")]})
        orig = workers.run_workers
        with patch("workers.run_workers", side_effect=lambda *x, **k: orig(*x, create=s, **k)):
            out = run(a, "compare")
        self.assertIn("final", out)
        last = [m for m in a.messages if m.get("role") == "tool"][-1]["content"]
        self.assertIn("A done", last)
        self.assertIn("B done", last)


if __name__ == "__main__":
    unittest.main()
