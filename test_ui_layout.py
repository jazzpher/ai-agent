"""Presentation regressions, no network or provider calls."""
import unittest
from unittest.mock import patch
import app
from agent import AIAgent
from ui_design import CSS, HEADER

class WorkspaceUITest(unittest.TestCase):
    def test_tabs_and_controls_exist_without_sandbox_creation(self):
        with patch.object(app.session_manager, 'get_or_create', side_effect=AssertionError('UI must stay lazy')):
            demo = app.build_app()
        config = demo.get_config_file()
        ids = {c['props'].get('elem_id') for c in config['components']}
        for name in ('agent-chat', 'composer', 'message-input', 'send-button', 'stop-button',
                     'settings-panel', 'session-panel', 'approval-banner', 'approval-actions'):
            self.assertIn(name, ids)
        tabs = [c['props']['label'] for c in config['components'] if c['type'] == 'tabitem']
        self.assertEqual(tabs, ['Chat', 'Settings', 'Session'])
        keys = [c for c in config['components'] if c['type'] == 'textbox' and c['props'].get('label') == 'API key']
        self.assertTrue(keys)
        self.assertTrue(all(c['props']['type'] == 'password' for c in keys))

    def test_stop_uses_plain_text(self):
        agent = AIAgent(api_key='fake')
        app.stop_chat(agent)
        self.assertTrue(agent.cancel_requested)
        self.assertEqual(app.stop_chat(agent)[1]['value'], 'Stop')

    def test_mobile_and_accessibility_assets(self):
        self.assertIn('100dvh', CSS)
        self.assertIn('safe-area-inset-bottom', CSS)
        self.assertIn(':focus-visible', CSS)
        self.assertIn('prefers-reduced-motion', CSS)
        self.assertIn('<svg', HEADER)
        self.assertNotIn('<script', HEADER)

if __name__ == '__main__':
    unittest.main()


class FilesPanelTest(unittest.TestCase):
    def test_list_workspace_files_newest_first(self):
        import tempfile, os, time
        import app as app_mod
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d, patch.object(app_mod, "WORKSPACE_DIR", d):
            self.assertIsNone(app_mod.list_workspace_files())
            a = os.path.join(d, "a.docx"); b = os.path.join(d, "b.txt")
            open(a, "w").write("1"); time.sleep(0.02); open(b, "w").write("2")
            os.utime(a, (1, 1))
            self.assertEqual(app_mod.list_workspace_files(), [b, a])
