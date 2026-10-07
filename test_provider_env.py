import json
import os
import tempfile
import unittest
from unittest.mock import patch
import providers


class EnvironmentProvidersTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.files = patch.object(providers, 'PROVIDERS_FILE', self.temp.name + '/providers.json')
        self.files.start()
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.nvidia = patch.object(providers, 'NVIDIA_API_KEY', '')
        self.nvidia.start()

    def tearDown(self):
        self.nvidia.stop()
        self.env.stop()
        self.files.stop()
        self.temp.cleanup()

    def test_env_survives_file_loss(self):
        item = dict(preset='Groq', base_url='https://api.groq.com/openai/v1',
                    model='test-model', api_key='fake-only', enabled=True)
        os.environ['AGENT_PROVIDERS_JSON'] = json.dumps([item])
        self.assertEqual(providers.active_providers(), [{**item, 'vision': False}])
        providers.save_providers([{**item, 'model': 'local-model'}])
        self.assertEqual(providers.active_providers()[0]['model'], 'local-model')
        os.unlink(providers.PROVIDERS_FILE)
        self.assertEqual(providers.active_providers(), [{**item, 'vision': False}])

    def test_vision_flag_defaults_off_and_round_trips(self):
        item = dict(preset='Gemini', base_url='https://example.invalid/v1',
                    model='m', api_key='fake-only', enabled=True)
        providers.save_providers([item, {**item, 'vision': True}])
        got = providers.active_providers()
        self.assertFalse(got[0]['vision'])
        self.assertTrue(got[1]['vision'])

    def test_env_vision_must_be_bool(self):
        item = dict(preset='Groq', base_url='https://example.invalid/v1',
                    model='m', api_key='fake-only', enabled=True, vision='yes')
        os.environ['AGENT_PROVIDERS_JSON'] = json.dumps([item])
        with self.assertRaises(ValueError):
            providers.load_providers()
        os.environ['AGENT_PROVIDERS_JSON'] = json.dumps([{**item, 'vision': True}])
        self.assertTrue(providers.load_providers()[0]['vision'])

    def test_invalid_env_is_sanitized(self):
        for raw in ('secret-text', '{}', '["secret-text"]', '[{"preset":"bad"}]',
                    '[{"api_key":123}]', '[{"enabled":"false"}]'):
            os.environ['AGENT_PROVIDERS_JSON'] = raw
            with self.assertRaises(ValueError) as exc:
                providers.load_providers()
            self.assertNotIn('secret-text', str(exc.exception))


def test_env_key_providers(monkeypatch):
    import providers
    monkeypatch.setattr(providers, "NVIDIA_API_KEY", "n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    got = [p["preset"] for p in providers.env_key_providers()]
    assert got == ["NVIDIA NIM", "Groq", "OpenRouter", "OpenRouter"]


def test_vision_only_slot_never_answers(monkeypatch):
    import providers, router
    monkeypatch.setattr(providers, "NVIDIA_API_KEY", "")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    items = providers.env_key_providers()
    assert [p.get("vision_only", False) for p in items] == [False, True]
    answering = router.rank_providers(items)
    assert [p["model"] for p in answering] == [items[0]["model"]]
    seeing = router.rank_providers(items, need_vision=True)
    assert seeing[0]["vision_only"] is True
