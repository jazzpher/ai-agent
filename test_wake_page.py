import json
import re
import unittest
from pathlib import Path

DOCS = Path(__file__).parent / 'docs'


class WakePageTests(unittest.TestCase):
    def setUp(self):
        self.html = (DOCS / 'index.html').read_text()

    def test_points_at_the_render_app_and_has_no_secrets(self):
        self.assertIn("https://private-ai-agent-9d5u.onrender.com/", self.html)
        for word in ('password', 'api_key', 'apikey', 'secret', 'token', 'AGENT_'):
            self.assertNotIn(word.lower(), self.html.lower())

    def test_override_is_loopback_only(self):
        self.assertIn("u.hostname === '127.0.0.1' || u.hostname === 'localhost'", self.html)

    def test_probe_cannot_mistake_an_html_page_for_ready(self):
        self.assertIn('new Image()', self.html)
        self.assertIn('/favicon.ico', self.html.replace("'favicon.ico", "'/favicon.ico"))

    def test_manifest_and_icons_exist(self):
        manifest = json.loads((DOCS / 'manifest.webmanifest').read_text())
        self.assertEqual(manifest['display'], 'standalone')
        for icon in manifest['icons']:
            self.assertTrue(icon['src'].startswith('data:image/png;base64,'))
        self.assertIn('rel="apple-touch-icon" href="data:image/png;base64,', self.html)

    def test_no_emoji_glyphs(self):
        self.assertIsNone(re.search('[\U0001F300-\U0001FAFF\u2600-\u27BF]', self.html))


if __name__ == '__main__':
    unittest.main()
