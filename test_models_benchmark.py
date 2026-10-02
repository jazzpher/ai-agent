"""Model list and benchmark tests with fake clients. No network."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import APIStatusError

import providers


def msg(content=None, tool_name=None, tool_args=None):
    calls = None
    if tool_name:
        calls = [SimpleNamespace(function=SimpleNamespace(name=tool_name, arguments=json.dumps(tool_args)))]
    return SimpleNamespace(content=content, tool_calls=calls)


def reply(m):
    return SimpleNamespace(choices=[SimpleNamespace(message=m)],
                           usage=SimpleNamespace(completion_tokens=7))


GOOD = [
    msg("PONG"),
    msg('```json\n{"total": 42, "items": ["x", "y"]}\n```'),
    msg(None, "get_weather", {"city": "Manila"}),
    msg("def add(a, b):\n    return a + b"),
    msg("Ang langit ay kulay asul sa umaga."),
]


class FakeClient:
    def __init__(self, replies=None, models=None, model_errors=()):
        self.replies = list(replies or [])
        self.models = SimpleNamespace(list=self._list)
        self._models = models or []
        self._errors = list(model_errors)
        self.model_calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.requests = []

    def _create(self, **kw):
        self.requests.append(kw)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return reply(item)

    def _list(self):
        self.model_calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return SimpleNamespace(data=[SimpleNamespace(id=i) for i in self._models])


def status(code):
    response = httpx.Response(code, request=httpx.Request("GET", "https://example.invalid/v1/models"))
    return APIStatusError("synthetic", response=response, body=None)


class ListModelsTest(unittest.TestCase):
    def test_sorted_unique_ids(self):
        c = FakeClient(models=["b-model", "a-model", "b-model"])
        self.assertEqual(providers.list_models("https://x/v1", "fake", client=c), ["a-model", "b-model"])

    def test_needs_url_and_key(self):
        with self.assertRaises(ValueError):
            providers.list_models("", "fake")
        with self.assertRaises(ValueError):
            providers.list_models("https://x/v1", "")

    def test_retries_transient_then_succeeds(self):
        c = FakeClient(models=["m"], model_errors=[status(503)])
        with patch.object(providers.time, "sleep", lambda s: None):
            self.assertEqual(providers.list_models("https://x/v1", "fake", client=c), ["m"])
        self.assertEqual(c.model_calls, 2)

    def test_permanent_error_not_retried(self):
        c = FakeClient(model_errors=[status(401)])
        with self.assertRaises(APIStatusError):
            providers.list_models("https://x/v1", "fake", client=c)
        self.assertEqual(c.model_calls, 1)


class BenchmarkTest(unittest.TestCase):
    def test_all_pass(self):
        c = FakeClient(GOOD)
        rows = providers.benchmark_model("https://x/v1", "m", "fake-key", client=c)
        self.assertEqual([r["passed"] for r in rows], [True] * 5)
        self.assertEqual(len(c.requests), 5)
        self.assertIn("tools", c.requests[2])
        self.assertNotIn("tools", c.requests[0])

    def test_wrong_answers_fail(self):
        bad = [msg("Sure! PONG is the word"), msg("not json"), msg("I cannot"), msg("add = 1"), msg("blue")]
        rows = providers.benchmark_model("https://x/v1", "m", "fake-key", client=FakeClient(bad))
        self.assertEqual([r["passed"] for r in rows], [False] * 5)

    def test_error_is_recorded_and_key_is_masked(self):
        err = status(400)
        replies = [err] + GOOD[1:]
        with patch.object(providers, "completion_with_retry",
                          side_effect=[RuntimeError("bad fake-key here")] + [reply(m) for m in GOOD[1:]]):
            rows = providers.benchmark_model("https://x/v1", "m", "fake-key", client=object())
        self.assertFalse(rows[0]["passed"])
        self.assertIn("***", rows[0]["error"])
        self.assertNotIn("fake-key", rows[0]["error"])
        self.assertTrue(all(r["passed"] for r in rows[1:]))

    def test_needs_inputs(self):
        with self.assertRaises(ValueError):
            providers.benchmark_model("https://x/v1", "", "fake")

    def test_format(self):
        rows = providers.benchmark_model("https://x/v1", "m", "fake-key", client=FakeClient(GOOD))
        text = providers.format_benchmark("m", rows)
        self.assertIn("5/5", text)
        self.assertNotIn("fake-key", text)

    def test_json_check_accepts_fences_only_when_valid(self):
        self.assertTrue(providers._check_json(msg('{"total": 42, "items": ["x","y"]}')))
        self.assertFalse(providers._check_json(msg('{"total": 41, "items": ["x","y"]}')))

    def test_no_calls_at_import(self):
        # Importing providers must not create clients or hit the network.
        self.assertTrue(callable(providers.benchmark_model))


if __name__ == "__main__":
    unittest.main()
