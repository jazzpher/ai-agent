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
        self.assertEqual(providers.active_providers(), [item])
        providers.save_providers([{**item, 'model': 'local-model'}])
        self.assertEqual(providers.active_providers()[0]['model'], 'local-model')
        os.unlink(providers.PROVIDERS_FILE)
        self.assertEqual(providers.active_providers(), [item])

    def test_invalid_env_is_sanitized(self):
        for raw in ('secret-text', '{}', '["secret-text"]', '[{"preset":"bad"}]',
                    '[{"api_key":123}]', '[{"enabled":"false"}]'):
            os.environ['AGENT_PROVIDERS_JSON'] = raw
            with self.assertRaises(ValueError) as exc:
                providers.load_providers()
            self.assertNotIn('secret-text', str(exc.exception))
