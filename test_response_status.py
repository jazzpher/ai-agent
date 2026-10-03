"""Provider and UI diagnostics tested with fakes only."""
import contextlib
import io
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock

import api_retry
import agent as agent_mod
from response_status import with_status
from test_agent_loop import FakeClient, make_agent, run, stream_chunks
from test_api_retry import status


def chunk(content=None, reasoning=None, tools=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        delta=SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=tools),
        finish_reason="stop")])


class StatusTest(unittest.TestCase):
    def tearDown(self):
        patch.stopall()

    def test_wait_during_blocked_creation_and_stream(self):
        closed = threading.Event()
        def stream():
            try:
                time.sleep(.04)
                yield "answer"
                time.sleep(.04)
            finally:
                closed.set()
        events = list(with_status(stream, deadline=time.monotonic()+1, interval=.01))
        self.assertIn(("item", "answer"), events)
        self.assertGreaterEqual(sum(k == "wait" for k, _ in events), 2)
        self.assertTrue(closed.wait(.2))

    def test_deadline_exits_even_if_stream_blocks(self):
        finished = threading.Event()
        def blocked():
            try:
                time.sleep(.08)
                yield "late answer"
            finally:
                finished.set()
        with self.assertRaises(TimeoutError):
            list(with_status(blocked, deadline=time.monotonic()+.02, interval=.005))
        self.assertTrue(finished.wait(.3))

    def test_cancel_exits_and_closes_worker(self):
        cancel = threading.Event()
        def stream():
            yield "one"
            cancel.set()
            yield "two"
        with self.assertRaises(api_retry.CompletionCancelled):
            list(with_status(stream, deadline=time.monotonic()+1, cancelled=cancel.is_set))

    def test_nemotron_default_off_and_explicit_on(self):
        for enabled in (False, True):
            client = FakeClient([("text", "hello")])
            a = make_agent(client)
            a.enable_thinking = enabled
            run(a, "hello")
            controls = {"enable_thinking": enabled}
            if enabled:
                controls["force_nonempty_content"] = True
            self.assertEqual(client.calls[0]["extra_body"], {"chat_template_kwargs": controls})

    def test_other_model_does_not_get_nemotron_flag(self):
        a = agent_mod.AIAgent(api_key="fake", model="plain-text")
        self.assertIsNone(a._thinking_body())
        a.model = "openai/gpt-oss-120b"
        self.assertEqual(a._thinking_body("medium"),
                         {"chat_template_kwargs": {"reasoning_effort": "medium"}})

    def test_reasoning_then_text_never_discloses_reasoning(self):
        client = FakeClient([])
        client.chat.completions.create = lambda **kw: iter([
            chunk(reasoning="PRIVATE_REASONING"), chunk(content="hello")])
        a = make_agent(client)
        outputs = list(a.chat_stream("hello"))
        self.assertTrue(any("thinking; waiting" in o for o in outputs))
        self.assertIn("hello", outputs[-1])
        self.assertNotIn("Waiting", outputs[-1])
        self.assertNotIn("PRIVATE_REASONING", "".join(outputs))
        self.assertEqual(a.messages[-1]["content"], "hello")

    def test_empty_and_reasoning_only_streams_have_visible_error(self):
        for pieces in ([], [chunk(reasoning="PRIVATE")]):
            client = FakeClient([])
            client.chat.completions.create = lambda **kw: iter(pieces)
            a = make_agent(client)
            outputs = list(a.chat_stream("hello"))
            self.assertIn("finished without an answer", outputs[-1])
            self.assertEqual(a.messages[-1]["role"], "user")
            self.assertNotIn("PRIVATE", "".join(outputs))

    def test_midstream_failure_not_replayed(self):
        client = FakeClient([])
        def broken():
            yield chunk(content="partial")
            raise status(503)
        client.chat.completions.create = Mock(return_value=broken())
        out = run(make_agent(client))
        self.assertIn("partial", out)
        self.assertIn("Stream error", out)
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_retry_status_visible_during_backoff(self):
        client = FakeClient([("text", "hello")])
        real = client.create
        calls = []
        def create(**kw):
            calls.append(kw)
            if len(calls) == 1:
                raise status(503)
            return real(**kw)
        client.chat.completions.create = create
        a = make_agent(client)
        # Keep the real backoff (1s) to verify a UI update, no network.
        outputs = list(a.chat_stream("hello"))
        self.assertTrue(any("Retrying (attempt 2/3" in o for o in outputs))
        self.assertIn("hello", outputs[-1])

    def test_safe_stdout_does_not_include_exception_or_request_data(self):
        client = Mock()
        client.chat.completions.create.side_effect = RuntimeError("SECRET PROMPT KEY")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(RuntimeError):
            api_retry.completion_with_retry(client, model="PRIVATE_MODEL", messages=["PRIVATE_PROMPT"])
        self.assertNotIn("SECRET", output.getvalue())
        self.assertNotIn("PRIVATE", output.getvalue())
        for line in output.getvalue().splitlines():
            self.assertIn("event", json.loads(line))
        a = agent_mod.AIAgent(api_key="fake")
        with contextlib.redirect_stdout(output):
            a._log("stream_error", error="SECRET")
            a._log("user_message", content="PRIVATE")
        self.assertNotIn("SECRET", output.getvalue())
        self.assertNotIn("PRIVATE", output.getvalue())

    def test_ui_yields_before_provider_refresh(self):
        import app
        a = make_agent(FakeClient([("text", "hello")]))
        a.refresh_providers = Mock()
        with patch.object(app, "format_metrics", return_value="metrics"):
            ui = app.chat_stream("hello", [], [], a)
            first, _ = next(ui)
            self.assertIn("Preparing", first[-1]["content"])
            a.refresh_providers.assert_not_called()
            list(ui)
            a.refresh_providers.assert_called_once()


if __name__ == "__main__":
    unittest.main()

class LazySandboxUITest(unittest.TestCase):
    def test_metrics_and_status_never_create_sandbox(self):
        import app
        import tools
        a = agent_mod.AIAgent(api_key="fake")
        with patch.object(agent_mod.session_manager, "get_or_create", side_effect=AssertionError("must not install")):
            self.assertEqual(a.get_metrics()["sandbox_mode"], "not started")
            self.assertIn("not started", app.format_sandbox_status(a))
            self.assertEqual(tools.get_sandbox_status(a.session_id)["mode"], "not started")

    def test_status_does_not_wait_for_creation_lock(self):
        from sandbox_session import SessionManager
        manager = SessionManager()
        manager._lock.acquire()
        try:
            self.assertEqual(manager.peek_status("new")["mode"], "not started")
        finally:
            manager._lock.release()

    def test_final_cleanup_preserves_answer_and_initial_yield_does_not_install(self):
        import app
        a = agent_mod.AIAgent(api_key="fake")
        completed = [{"role": "user", "content": "hello"},
                     {"role": "assistant", "content": "Hello!"}]
        with patch.object(agent_mod.session_manager, "get_or_create", side_effect=AssertionError("must not install")), \
             patch.object(app, "chat_stream", return_value=iter([(completed, "metrics")])):
            output = list(app.start_chat("hello", [], [], a))
            self.assertEqual(output[-1][0], completed)
