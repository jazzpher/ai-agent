"""Keep-alive, GeneratorExit safety, readable errors, sandbox package visibility."""
import os
import sys
import threading
import time
import unittest

import agent as agent_mod


class SummarizeErrorTest(unittest.TestCase):
    def test_bare_errors_header_is_not_the_summary(self):
        out = "Errors:\nTraceback (most recent call last):\n  File \"<string>\", line 1\nOSError: cannot open resource"
        s = agent_mod._summarize_result({"status": "error", "output": out})
        self.assertIn("OSError: cannot open resource", s)
        self.assertNotEqual(s.strip(), "error — Errors:")

    def test_plain_error_still_works(self):
        s = agent_mod._summarize_result({"status": "error", "output": "Timed out after 30s"})
        self.assertIn("Timed out after 30s", s)


class KeepaliveTest(unittest.TestCase):
    def setUp(self):
        try:
            import app
        except Exception as e:  # gradio not installed
            self.skipTest(f"app import failed: {e}")
        self.app = app

    def test_beats_while_silent_then_items(self):
        def slow():
            yield 1
            time.sleep(0.35)
            yield 2
        got = list(self.app.keepalive(slow(), interval=0.1))
        kinds = [k for k, _ in got]
        self.assertIn("beat", kinds)
        self.assertEqual([v for k, v in got if k == "item"], [1, 2])

    def test_worker_error_is_raised(self):
        def bad():
            yield 1
            raise ValueError("boom")
        with self.assertRaises(ValueError):
            list(self.app.keepalive(bad(), interval=0.1))

    def test_close_cancels(self):
        flag = []
        def forever():
            while True:
                time.sleep(0.05)
                yield 1
        g = self.app.keepalive(forever(), interval=0.05, cancel=lambda: flag.append(1))
        next(g)
        g.close()
        self.assertEqual(flag, [1])

    def test_start_chat_does_not_yield_after_disconnect(self):
        app = self.app
        class A:
            cancel_requested = False
            def cancel(self): self.cancel_requested = True
        def fake_stream(*a, **k):
            for i in range(100):
                time.sleep(0.02)
                yield ([{"role": "assistant", "content": str(i)}], "m")
        orig_chat, orig_fm = app.chat_stream, app.format_metrics
        app.chat_stream, app.format_metrics = fake_stream, lambda a: "m"
        try:
            g = app.start_chat("hi", [], [], A())
            next(g); next(g)
            g.close()  # would raise RuntimeError("generator ignored GeneratorExit")
        finally:
            app.chat_stream, app.format_metrics = orig_chat, orig_fm


class VenvSitePackagesTest(unittest.TestCase):
    def test_app_venv_packages_visible_in_sandbox(self):
        import sandbox_session as S
        if sys.prefix == getattr(sys, "base_prefix", sys.prefix):
            self.skipTest("tests not running inside a venv")
        sb = S.SessionSandbox(force_mode="venv")
        r = sb.run_python(
            "import importlib.util as u;"
            "print([m for m in ('PIL','docx') if u.find_spec(m) is None])")
        self.assertIn("[]", r["output"])


if __name__ == "__main__":
    unittest.main()
