"""Palette regressions. Browser checks live in checks/contrast_check.py."""
import unittest
from pathlib import Path
from ui_design import CSS, workspace_theme, LOGIN_MESSAGE


def luminance(color):
    channels = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
    return sum((c/12.92 if c <= .04045 else ((c+.055)/1.055)**2.4)*w
               for c, w in zip(channels, (.2126, .7152, .0722)))


def contrast(a, b):
    values = sorted((luminance(a), luminance(b)))
    return (values[1]+.05)/(values[0]+.05)


class ThemeContrastTest(unittest.TestCase):
    def test_native_and_auth_foregrounds_match_surfaces(self):
        theme = workspace_theme().to_dict()['theme']
        for suffix in ('', '_dark'):
            for foreground, background in (
                ('body_text_color', 'body_background_fill'),
                ('body_text_color', 'input_background_fill'),
                ('block_label_text_color', 'block_background_fill'),
                ('body_text_color_subdued', 'background_fill_primary'),
                ('input_placeholder_color', 'input_background_fill'),
                ('button_primary_text_color', 'button_primary_background_fill'),
            ):
                self.assertGreaterEqual(contrast(theme[foreground+suffix], theme[background+suffix]), 4.5)

    def test_css_tracks_gradio_dark_class(self):
        self.assertIn('.dark .gradio-container, .gradio-container.dark', CSS)
        self.assertIn('#agent-chat code span {color:var(--ws-ink);}', CSS)
        self.assertIn('color:var(--ws-on-accent)', CSS)
        self.assertNotIn('background:white', CSS)

    def test_login_uses_static_html_without_custom_auth_logic(self):
        self.assertIn('id="workspace-login"', LOGIN_MESSAGE)
        self.assertNotIn('<script', LOGIN_MESSAGE)
        self.assertNotIn('<form', LOGIN_MESSAGE)
        self.assertNotIn('fetch(', LOGIN_MESSAGE)
        from server_settings import launch_settings
        self.assertEqual(launch_settings({})['auth_message'], LOGIN_MESSAGE)

    def test_mobile_composer_wraps_and_scrolls(self):
        self.assertIn('white-space:pre-wrap; overflow-wrap:anywhere; overflow-y:auto', CSS)
        self.assertIn('#examples input {font-size:12px !important', CSS)
        self.assertIn('font-size:13px !important', CSS)

    def test_wake_has_paired_palettes(self):
        html = (Path(__file__).parent/'docs'/'index.html').read_text()
        self.assertIn('prefers-color-scheme:dark', html)
        self.assertIn('color-scheme: light dark', html)
        self.assertIn('color:var(--on-green)', html)

if __name__ == '__main__':
    unittest.main()
