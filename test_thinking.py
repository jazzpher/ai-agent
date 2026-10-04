"""Thinking panel: display only, no network."""
import unittest
from types import SimpleNamespace
import app
import thinking
from test_agent_loop import FakeClient, make_agent
from test_response_status import chunk
from ui_design import CSS


class ThinkingPanelTest(unittest.TestCase):
    def test_live_then_collapsed(self):
        a = SimpleNamespace()
        thinking.add(a, "step <one>")
        live = thinking.render(a)
        self.assertIn("Thinking... ", live)
        self.assertIn(" open", live.split(">")[0] + ">")
        self.assertIn("step &lt;one&gt;", live)
        thinking.finish(a)
        done = thinking.render(a)
        self.assertRegex(done, r"Thought for \d+ seconds?")
        self.assertNotIn(" open", done.split(">")[0] + ">")

    def test_nothing_without_reasoning(self):
        a = SimpleNamespace()
        self.assertEqual(thinking.render(a), "")
        thinking.add(a, "   ")
        self.assertEqual(thinking.render(a), "")

    def test_agent_shows_reasoning_but_keeps_it_out_of_history(self):
        client = FakeClient([])
        client.chat.completions.create = lambda **kw: iter([
            chunk(reasoning="MY_THOUGHTS"), chunk(content="hello")])
        a = make_agent(client)
        outputs = list(a.chat_stream("hi"))
        self.assertNotIn("MY_THOUGHTS", "".join(outputs))
        self.assertIn("MY_THOUGHTS", thinking.render(a))
        self.assertIn("Thought for", thinking.render(a))
        self.assertNotIn("MY_THOUGHTS", str(a.messages))
        # next turn starts clean
        client.chat.completions.create = lambda **kw: iter([chunk(content="plain")])
        list(a.chat_stream("again"))
        self.assertEqual(thinking.render(a), "")

    def test_app_prepends_panel_only_with_reasoning(self):
        client = FakeClient([])
        client.chat.completions.create = lambda **kw: iter([chunk(reasoning="R1"), chunk(content="ok")])
        a = make_agent(client)
        last = list(app.chat_stream("hi", [], None, a))[-1][0][-1]["content"]
        self.assertIn("Thought for", last)
        self.assertIn("ok", last)
        client.chat.completions.create = lambda **kw: iter([chunk(content="ok")])
        last = list(app.chat_stream("hi", [], None, a))[-1][0][-1]["content"]
        self.assertNotIn("Thought", last)

    def test_css(self):
        self.assertIn(".think-body", CSS)
