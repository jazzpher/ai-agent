import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import httpx
from openai import APIStatusError, APIConnectionError, APITimeoutError
import api_retry
import providers


def status(code):
    response = httpx.Response(code, request=httpx.Request('POST', 'https://example.invalid/v1/chat/completions'))
    return APIStatusError('synthetic failure', response=response, body=None)


class RetryTest(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.clock = patch.object(api_retry.time, 'monotonic', lambda: self.now)
        self.sleep = patch.object(api_retry.time, 'sleep', self.advance)
        self.clock.start()
        self.sleep.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(self.sleep.stop)
        self.client = Mock()

    def advance(self, delay):
        self.now += delay

    def test_all_gateway_statuses_recover(self):
        for code in (429, 500, 502, 503, 504):
            with self.subTest(code=code):
                self.client.reset_mock()
                self.client.chat.completions.create.side_effect = [status(code)] * 2 + ['ok']
                self.assertEqual(api_retry.completion_with_retry(self.client, model='test'), 'ok')
                self.assertEqual(self.client.chat.completions.create.call_count, 3)

    def test_exhausted_waits_three_seconds_and_three_attempts(self):
        self.client.chat.completions.create.side_effect = status(503)
        with self.assertRaises(APIStatusError):
            api_retry.completion_with_retry(self.client)
        self.assertEqual(self.now, 3)
        self.assertEqual(self.client.chat.completions.create.call_count, 3)

    def test_nontransient_is_not_retried(self):
        for code in (400, 401, 403, 404, 422, 501):
            self.client.reset_mock()
            self.client.chat.completions.create.side_effect = status(code)
            with self.assertRaises(APIStatusError):
                api_retry.completion_with_retry(self.client)
            self.assertEqual(self.client.chat.completions.create.call_count, 1)
        self.assertEqual(self.now, 0)

    def test_connection_timeout_retries(self):
        request = httpx.Request('POST', 'https://example.invalid')
        self.client.chat.completions.create.side_effect = [APIConnectionError(request=request), APITimeoutError(request=request), 'ok']
        self.assertEqual(api_retry.completion_with_retry(self.client), 'ok')
        self.assertEqual(self.now, 3)

    def test_client_timeout_and_no_hidden_retries(self):
        with patch.object(api_retry, 'OpenAI') as factory:
            api_retry.make_client('https://example.invalid', 'fake-key')
        self.assertEqual(factory.call_args.kwargs['timeout'], 30)
        self.assertEqual(factory.call_args.kwargs['max_retries'], 0)

    def test_deadline_prevents_another_attempt(self):
        self.client.chat.completions.create.side_effect = status(503)
        with self.assertRaises(APIStatusError):
            api_retry.completion_with_retry(self.client, deadline=1)
        self.assertEqual(self.client.chat.completions.create.call_count, 1)
        self.assertEqual(self.client.chat.completions.create.call_args.kwargs['timeout'], 1)

    def test_cancel_during_wait(self):
        self.client.chat.completions.create.side_effect = status(503)
        with self.assertRaises(api_retry.CompletionCancelled):
            api_retry.completion_with_retry(self.client, cancelled=lambda: self.now >= 0.5)
        self.assertEqual(self.client.chat.completions.create.call_count, 1)
        self.assertEqual(self.now, 0.5)

    def test_stream_returned_once_and_not_consumed(self):
        def broken_stream():
            yield 'first chunk'
            raise status(503)
        stream = broken_stream()
        self.client.chat.completions.create.return_value = stream
        returned = api_retry.completion_with_retry(self.client, stream=True)
        self.assertEqual(next(returned), 'first chunk')
        with self.assertRaises(APIStatusError):
            next(returned)
        self.assertEqual(self.client.chat.completions.create.call_count, 1)

    def test_nvidia_default_and_other_presets_unchanged(self):
        self.assertEqual(providers.PRESETS['NVIDIA NIM'][1], 'nvidia/nemotron-3-ultra-550b-a55b')
        self.assertEqual(providers.PRESETS['Groq'][1], 'openai/gpt-oss-120b')
        with patch.dict('os.environ', {'AI_MODEL': 'custom-model'}):
            scope = {}
            exec(Path('config.py').read_text(), {'__file__': str(Path('config.py').absolute())}, scope)
            self.assertEqual(scope['DEFAULT_MODEL'], 'custom-model')

    def test_fallback_after_exhaustion(self):
        # Load real stream loop without importing optional document/ML packages.
        tree = ast.parse(Path('agent.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AIAgent')
        selected = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in ('chat_stream', '_switch_provider', '_apply_provider', '_thinking_body')]
        module = ast.Module(body=[ast.ClassDef(name='AIAgent', bases=[], keywords=[], body=selected, decorator_list=[])], type_ignores=[])
        scope = dict(time=api_retry.time, MAX_TOTAL_SECONDS=120, MAX_ITERATIONS=2, REPEAT_CALL_LIMIT=5, DEFAULT_TEMPERATURE=0.3,
                     DEFAULT_MAX_TOKENS=8192, TOOL_DEFINITIONS=[], _model_supports_reasoning=lambda m: False,
                     completion_with_retry=api_retry.completion_with_retry, is_transient=api_retry.is_transient,
                     CompletionCancelled=api_retry.CompletionCancelled,
                     with_status=__import__("response_status").with_status)
        exec(compile(ast.fix_missing_locations(module), 'agent.py', 'exec'), scope)
        agent = scope['AIAgent']()
        agent.enable_thinking=False; agent.reasoning_effort='high'; agent.api_key='fake'; agent.model='first'; agent.errors=0; agent.messages=[]; agent.system_prompt='test'
        agent.context=SimpleNamespace(build_context_block=lambda: '', set_goal=lambda g: None)
        agent._trim_conversation_history=lambda: None; agent._load_memory=lambda: None; agent._should_analyze=lambda *a: False
        agent._log=Mock(); agent._provider_idx=0
        agent._providers=[dict(model='first', base_url='https://example.invalid', api_key='fake'), dict(model='second', base_url='https://example.invalid', api_key='fake')]
        first=Mock(); first.chat.completions.create.side_effect=status(503)
        second=Mock(); second.chat.completions.create.side_effect=status(401)
        agent._get_client=lambda: (first, second)[agent._provider_idx]
        result=list(agent.chat_stream('hi'))
        self.assertEqual(first.chat.completions.create.call_count, 3)
        self.assertEqual(second.chat.completions.create.call_count, 1)
        self.assertEqual(agent.model, 'second')
        self.assertTrue(any('Switching to fallback' in r for r in result))


if __name__ == '__main__':
    unittest.main()
