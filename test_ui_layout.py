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


class MobileChatHeightTests(unittest.TestCase):
    def test_mobile_chat_gets_more_height_and_extras_collapse(self):
        from ui_design import CSS
        self.assertIn('#extras', CSS)
        self.assertIn('calc(100dvh - 156px)', CSS)
        self.assertIn('#conversation-heading {display:none;}', CSS)
        self.assertIn('#render-notice {display:none;}', CSS)


class ActivityBarTests(unittest.TestCase):
    def test_phase_and_markup(self):
        import time
        import app as a
        self.assertEqual(a.activity_phase("x\n\n⏳ Waiting for the model…\n\n"), "waiting for the model")
        self.assertEqual(a.activity_phase("⏳ Model is thinking; waiting for answer…"), "thinking")
        self.assertEqual(a.activity_phase("🔧 **Action:** `bash`"), "running a tool")
        self.assertEqual(a.activity_phase("hello"), "writing")
        ag = AIAgent(api_key='fake')
        self.assertFalse(a.activity_html(ag)["visible"])
        ag._ui_running = True; ag._ui_started = time.monotonic() - 65
        ag._ui_history = [{"role": "assistant", "content": "⏳ Waiting for the model…"}]
        out = a.activity_html(ag)
        self.assertTrue(out["visible"])
        for part in ("act-dots", "1:05", "waiting for the model", "act-lost", "last update"):
            self.assertIn(part, out["value"])
        ag._ui_activity_changed = time.monotonic() - 60
        self.assertIn("still connected", a.activity_html(ag)["value"])

    def test_css_motion_safe(self):
        self.assertIn("act-bounce", CSS)
        self.assertIn("act-lost-show", CSS)
        self.assertIn(".act-dots i {animation:none", CSS)
