"""Context compaction tests. No network, no real keys."""
import json
import os
import unittest
from unittest.mock import patch

import compaction
from compaction import SUMMARY_TAG
from test_agent_loop import FakeClient, make_agent, run


def call(i, name="list_files", args=None):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": f"c{i}", "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args or {"path": f"f{i}.py"})}}]}


def result(i, text="ok", status="success"):
    return {"role": "tool", "tool_call_id": f"c{i}",
            "content": json.dumps({"status": status, "output": text})}


def filler(chars, seed=0):
    """Text that tokenizes like real output (not one repeated letter)."""
    words = " ".join(f"line{seed}_{k} value={k * 7 % 13}" for k in range(chars // 10 + 1))
    return words[:chars]


def history(n, big=0):
    msgs = [{"role": "system", "content": "SYS"},
            {"role": "user", "content": "Build a todo app with add, list and delete."}]
    for i in range(n):
        msgs += [call(i), result(i, filler(big or 600, i))]
    return msgs


def well_formed(messages):
    """Every tool result follows the assistant call that asked for it."""
    open_ids = set()
    for m in messages:
        if m.get("role") == "assistant":
            open_ids = {c["id"] for c in m.get("tool_calls") or []}
        elif m.get("role") == "tool":
            if m.get("tool_call_id") not in open_ids:
                return False
        else:
            open_ids = set()
    return True


class SettingsTest(unittest.TestCase):
    def test_defaults_and_bad_values(self):
        with patch.dict(os.environ, {"AGENT_CONTEXT_TOKENS": "abc", "AGENT_COMPACT_AT": "5",
                                     "AGENT_COMPACT_KEEP": "1"}):
            self.assertEqual(compaction.context_tokens(), 32000)
            self.assertEqual(compaction.threshold_ratio(), 0.75)
            self.assertEqual(compaction.keep_recent(), 2)

    def test_threshold(self):
        msgs = history(2)
        with patch.dict(os.environ, {"AGENT_CONTEXT_TOKENS": "100000"}):
            self.assertFalse(compaction.needs_compaction(msgs))
            self.assertTrue(compaction.needs_compaction(msgs, extra_tokens=80000))
            self.assertTrue(compaction.needs_compaction(msgs, max_messages=3))


class CompactTest(unittest.TestCase):
    def test_keeps_system_request_and_recent_steps(self):
        msgs = history(10)
        res = compaction.compact(msgs, lambda p: "CHECKLIST: [x] add - add.py written", keep=4)
        new = res.messages
        self.assertEqual(new[0]["content"], "SYS")
        self.assertIs(new[1], msgs[1])
        self.assertTrue(new[2]["content"].startswith(SUMMARY_TAG))
        self.assertIn("[x] add - add.py written", new[2]["content"])
        self.assertEqual(new[3:], msgs[-4:])
        self.assertEqual(res.folded, 16)
        self.assertFalse(res.fallback)
        self.assertTrue(well_formed(new))

    def test_window_never_opens_on_a_tool_result(self):
        msgs = history(6)
        msgs[-2]["tool_calls"].append({"id": "c5b", "type": "function",
                                       "function": {"name": "read_file", "arguments": "{}"}})
        msgs.append({"role": "tool", "tool_call_id": "c5b", "content": "{}"})
        res = compaction.compact(msgs, lambda p: "summary text long enough here", keep=2)
        window = res.messages[3:]
        self.assertEqual(window[0]["role"], "assistant")
        self.assertTrue(well_formed(res.messages))

    def test_request_and_earlier_summary_reach_the_summarizer(self):
        msgs = history(8)
        seen = []
        first = compaction.compact(msgs, lambda p: seen.append(p) or "CHECKLIST: [ ] delete - not started", keep=2)
        more = first.messages + [call(50), result(50, filler(600, 50)), call(51), result(51, filler(600, 51)), call(52), result(52, filler(600, 52))]
        compaction.compact(more, lambda p: seen.append(p) or "CHECKLIST: [x] delete - test passed", keep=2)
        prompt = seen[1][1]["content"]
        self.assertIn("Build a todo app", prompt)
        self.assertIn("EARLIER SUMMARY", prompt)
        self.assertIn("[ ] delete - not started", prompt)
        self.assertIn("Never drop an item", seen[1][0]["content"])

    def test_second_compaction_replaces_the_first_summary(self):
        msgs = history(8)
        first = compaction.compact(msgs, lambda p: "first summary, long enough", keep=2).messages
        more = first + [call(50), result(50, filler(600, 50)), call(51), result(51, filler(600, 51)), call(52), result(52, filler(600, 52))]
        second = compaction.compact(more, lambda p: "second summary, long enough", keep=2).messages
        summaries = [m for m in second if compaction.is_summary(m)]
        self.assertEqual(len(summaries), 1)
        self.assertIn("second summary", summaries[0]["content"])
        self.assertIs(second[1], msgs[1])

    def test_fallback_when_the_model_call_fails(self):
        def boom(prompt):
            raise RuntimeError("503")
        msgs = history(6)
        msgs.insert(4, call(99, "write_file", {"path": "app.py", "content": "x"}))
        msgs.insert(5, result(99))
        res = compaction.compact(msgs, boom, notes="Plan status:\n1. add [done]", keep=2)
        text = res.messages[2]["content"]
        self.assertTrue(res.fallback)
        self.assertIn("REQUEST: Build a todo app", text)
        self.assertIn("FILES: app.py", text)
        self.assertIn("1. add [done]", text)
        self.assertIn("-> success", text)

    def test_empty_summary_uses_fallback(self):
        res = compaction.compact(history(6), lambda p: "  ", keep=2)
        self.assertTrue(res.fallback)

    def test_too_little_to_fold(self):
        self.assertIsNone(compaction.compact(history(1), lambda p: "x" * 50, keep=2))
        self.assertIsNone(compaction.compact([], lambda p: "x"))

    def test_shrinks_and_saves_tokens(self):
        msgs = history(20, big=5000)
        with patch.dict(os.environ, {"AGENT_CONTEXT_TOKENS": "6000"}):
            res = compaction.compact(msgs, lambda p: "summary of earlier steps, long enough", keep=6)
        self.assertLess(res.after, res.before)
        self.assertLess(res.after, 6000 * 0.75)
        last = res.messages[-1]["content"]
        self.assertNotIn("chars cut at compaction", last)  # the latest result stays whole
        # old big results are folded into the summary, leaving headroom below the trigger
        self.assertFalse(compaction.needs_compaction(res.messages))

    def test_prompt_is_bounded(self):
        msgs = history(200, big=1100)
        prompt = compaction.build_prompt(msgs[1], msgs[2:], limit=8000)
        self.assertLess(len(prompt[1]["content"]), 8000 * 2 + 6000)
        self.assertIn("earliest steps left out", prompt[1]["content"])


class AgentLoopCompactionTest(unittest.TestCase):
    def tearDown(self):
        from unittest.mock import patch as p
        p.stopall()

    def test_long_task_compacts_and_still_finishes(self):
        steps = [("tool", "run_python", {"code": f"print({i})" + "  # pad" * 120}) for i in range(5)] + [("text", "all done")]
        client = FakeClient(steps, sync=["CHECKLIST: [x] list files - list_files ran 5 times"] * 10)
        a = make_agent(client)
        a.max_context_messages = 3
        with patch.dict(os.environ, {"AGENT_COMPACT_KEEP": "2"}):
            out = run(a, "run five python prints, then say all done")
        self.assertGreaterEqual(a.compactions, 1)
        self.assertIn("Context compacted", out)
        self.assertIn("all done", out)
        self.assertTrue(any(compaction.is_summary(m) for m in a.messages))
        self.assertTrue(any(m.get("role") == "user" and
                            str(m.get("content", "")).startswith("run five python prints")
                            for m in a.messages))
        self.assertTrue(well_formed(a.messages))
        sent = [c for c in client.calls if c.get("stream")][-1]["messages"]
        self.assertTrue(well_formed(sent))
        self.assertTrue(any(compaction.is_summary(m) for m in sent))

    def test_no_compaction_when_it_would_not_save(self):
        msgs = history(2)
        self.assertIsNone(compaction.compact(msgs, lambda p: filler(20000), keep=2))

    def test_short_task_does_not_compact(self):
        client = FakeClient([("text", "hi")])
        a = make_agent(client)
        out = run(a, "say hi")
        self.assertEqual(a.compactions, 0)
        self.assertNotIn("Context compacted", out)


if __name__ == "__main__":
    unittest.main()


class TargetTest(unittest.TestCase):
    """A compaction leaves real headroom, so the next steps don't compact again."""

    def _history(self, steps=24, size=1400):
        msgs = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "build the inventory package"}]
        for i in range(steps):
            msgs.append({"role": "assistant", "content": "",
                         "tool_calls": [{"id": f"c{i}", "type": "function",
                                         "function": {"name": "read_file", "arguments": "{}"}}]})
            msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"out{i} " + "x" * size})
        return msgs

    def test_shrinks_to_target_not_just_under_trigger(self):
        import compaction as c
        from unittest import mock
        with mock.patch.dict(os.environ, {"AGENT_CONTEXT_TOKENS": "12000",
                                          "AGENT_COMPACT_KEEP": "6"}):
            msgs = self._history()
            while c.estimate(msgs, 3000) < 12000 * 0.75:
                msgs = self._history(len(msgs))
            res = c.compact(msgs, lambda p: "summary of earlier work " * 10,
                            extra_tokens=3000)
            self.assertIsNotNone(res)
            self.assertLessEqual(res.after, 12000 * c.target_ratio())
            self.assertFalse(c.needs_compaction(res.messages, 3000))
            # the latest step is kept whole, and the window never opens on a tool result
            self.assertEqual(res.messages[-1]["content"], msgs[-1]["content"])
            first_kept = res.messages.index(next(m for m in res.messages if c.is_summary(m))) + 1
            self.assertNotEqual(res.messages[first_kept].get("role"), "tool")

    def test_target_below_trigger(self):
        import compaction as c
        from unittest import mock
        with mock.patch.dict(os.environ, {"AGENT_COMPACT_TARGET": "0.9"}):
            self.assertLess(c.target_ratio(), c.threshold_ratio())
