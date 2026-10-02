import unittest
from server_settings import launch_settings


class LaunchSettingsTest(unittest.TestCase):
    def test_local_defaults(self):
        s = launch_settings({})
        self.assertEqual(s['server_name'], '127.0.0.1')
        self.assertIsNone(s['auth'])
        self.assertFalse(s['share'])

    def test_external_fails_closed(self):
        for e in ({'RENDER': 'true'}, {'AGENT_HOST': '0.0.0.0'},
                  {'AGENT_HOST': '192.168.1.5'}, {'AGENT_USERNAME': 'owner'},
                  {'AGENT_PASSWORD': 'x' * 20}):
            with self.assertRaises(ValueError):
                launch_settings(e)

    def test_strong_login(self):
        e = {'RENDER': 'true', 'PORT': '10000', 'AGENT_USERNAME': 'owner',
             'AGENT_PASSWORD': 'test-only-password-123'}
        s = launch_settings(e)
        self.assertEqual(s['server_name'], '0.0.0.0')
        self.assertEqual(s['server_port'], 10000)
        self.assertTrue(s['auth']('owner', e['AGENT_PASSWORD']))
        self.assertFalse(s['auth']('owner', 'wrong'))
        self.assertFalse(s['auth']('wrong', e['AGENT_PASSWORD']))
        self.assertFalse(s['inbrowser'])

    def test_unicode(self):
        s = launch_settings({'AGENT_USERNAME': 'jazz', 'AGENT_PASSWORD': 'é' * 16})
        self.assertTrue(s['auth']('jazz', 'é' * 16))

    def test_bad_port(self):
        for p in ('bad', '0', '65536'):
            with self.assertRaises(ValueError):
                launch_settings({'PORT': p})
