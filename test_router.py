"""Routing by model size, picker ownership, fallback, vision describe, answered-by footer. No network."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import router
import vision
import agent as agent_mod
from test_agent_loop import FakeClient, make_agent, run

NV = {"preset": "NVIDIA NIM", "base_url": "https://nv", "model": "nvidia/nemotron-3-ultra", "api_key": "k1", "enabled": True}
GR = {"preset": "Groq", "base_url": "https://gr", "model": "openai/gpt-oss-120b", "api_key": "k2", "enabled": True}
GM = {"preset": "Gemini", "base_url": "https://gm", "model": "gemini-2.5-flash", "api_key": "k3", "enabled": True, "vision": True}
SM = {"preset": "OpenRouter", "base_url": "https://or", "model": "meta-llama/llama-3.2-3b-instruct:free", "api_key": "k4", "enabled": True}


class SizeTest(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(router.model_size_b("openai/gpt-oss-120b"), 120)
        self.assertEqual(router.model_size_b("meta-llama/llama-3.2-3b-instruct:free"), 3)
        self.assertEqual(router.model_size_b("llama-3.3-70b-versatile"), 70)
        self.assertGreater(router.model_size_b("nvidia/nemotron-3-ultra"), 120)
        self.assertEqual(router.model_size_b("some-unknown-model"), router.DEFAULT_SIZE)
        self.assertEqual(router.model_size_b(""), router.DEFAULT_SIZE)
        self.assertLess(router.model_size_b("gemini-2.0-flash-lite"), router.model_size_b("gemini-2.5-flash"))
        self.assertLess(router.model_size_b("gemma-3-27b-it"), 30)

    def test_rank_biggest_first_and_stable(self):
        ranked = router.rank_providers([SM, GM, GR, NV])
        self.assertEqual([p["preset"] for p in ranked], ["NVIDIA NIM", "Groq", "Gemini", "OpenRouter"])
        twin = dict(GR, preset="Groq2")
        self.assertEqual([p["preset"] for p in router.rank_providers([GR, twin])], ["Groq", "Groq2"])

    def test_rank_vision_first(self):
        self.assertEqual(router.rank_providers([NV, GR, GM], need_vision=True)[0]["preset"], "Gemini")

    def test_order_mode_keeps_saved_order(self):
        with patch.dict(os.environ, {"AGENT_ROUTING": "order"}):
            self.assertEqual(router.rank_providers([SM, NV]), [SM, NV])

    def test_fallback_skips_small_until_last(self):
        self.assertEqual(router.next_fallback([NV, SM, GR], 0), 2)
        self.assertEqual(router.next_fallback([NV, SM], 0), 1)
        self.assertIsNone(router.next_fallback([NV], 0))

    def test_footer(self):
        f = router.answered_by("openai/gpt-oss-120b", "Groq", True)
        self.assertIn("gpt-oss-120b", f)
        self.assertIn("Groq", f)
        self.assertIn("fallback", f)
        self.assertNotIn("fallback", router.answered_by("m", "p", False))


class AgentRoutingTest(unittest.TestCase):
    def tearDown(self):
        patch.stopall()

    def agent_with(self, provs):
        a = agent_mod.AIAgent(api_key="x")
        with patch("providers.active_providers", return_value=list(provs)):
            a.refresh_providers()
        return a

    def test_refresh_picks_biggest(self):
        a = self.agent_with([SM, GM, GR, NV])
        self.assertEqual((a.model, a.api_key), ("nvidia/nemotron-3-ultra", "k1"))

    def test_picker_uses_owning_providers_key(self):
        a = self.agent_with([NV, GR])
        a.use_model("openai/gpt-oss-120b")
        self.assertEqual((a.base_url, a.api_key, a.provider_name), ("https://gr", "k2", "Groq"))

    def test_unknown_picker_model_stays_on_current_provider(self):
        a = self.agent_with([NV, GR])
        self.assertFalse(a.use_model("deepseek-ai/deepseek-v4.1-flash"))
        self.assertEqual((a.model, a.api_key), ("deepseek-ai/deepseek-v4.1-flash", "k1"))

    def test_fallback_walks_down_by_size(self):
        a = self.agent_with([SM, GM, GR, NV])
        self.assertTrue(a._switch_provider())
        self.assertEqual(a.model, "openai/gpt-oss-120b")
        self.assertTrue(a._fell_back)
        self.assertTrue(a._switch_provider())
        self.assertEqual(a.model, "gemini-2.5-flash")
        self.assertTrue(a._switch_provider())
        self.assertEqual(a.model, SM["model"])
        self.assertFalse(a._switch_provider())

    def test_final_answer_shows_model(self):
        a = make_agent(FakeClient([("text", "hello")]))
        a.model, a.provider_name = "nvidia/nemotron-3-ultra", "NVIDIA NIM"
        out = run(a)
        self.assertIn("hello", out)
        self.assertIn("Sumagot: `nvidia/nemotron-3-ultra`", out)

    def test_footer_flags_fallback(self):
        a = make_agent(FakeClient([("text", "hello")]))
        a._fell_back = True
        self.assertIn("fallback", run(a))


class VisionDescribeTest(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.img = os.path.join(tempfile.mkdtemp(), "shot.png")
        Image.new("RGB", (40, 20), "white").save(self.img)

    def fake(self, text="Screenshot: button 'Save'", boom=False):
        calls = []
        def factory(p):
            def create(**kw):
                calls.append((p["preset"], kw))
                if boom:
                    raise RuntimeError("bad key k3")
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
            return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        return factory, calls

    def test_describes_with_vision_provider_only(self):
        factory, calls = self.fake()
        text, who = vision.describe_image(self.img, "ano ito", providers=[NV, GM], client_factory=factory)
        self.assertIn("Save", text)
        self.assertIn("Gemini", who)
        self.assertEqual([c[0] for c in calls], ["Gemini"])
        self.assertEqual(calls[0][1]["messages"][0]["content"][1]["type"], "image_url")

    def test_no_vision_provider(self):
        text, why = vision.describe_image(self.img, providers=[NV, GR])
        self.assertIsNone(text)
        self.assertIn("no vision", why)

    def test_error_does_not_leak_key(self):
        factory, _ = self.fake(boom=True)
        text, why = vision.describe_image(self.img, providers=[GM], client_factory=factory)
        self.assertIsNone(text)
        self.assertNotIn("k3", why)

    def test_text_model_gets_description_in_tool_result(self):
        a = make_agent(FakeClient([]))
        a._providers = [NV, GM]
        result = {"output": "image file", "image_path": self.img}
        factory, _ = self.fake("A login form with a Sign in button")
        with patch("api_retry.make_client", side_effect=lambda u, k: factory({"preset": "Gemini"})):
            self.assertTrue(a._describe_with_vision_model(result))
        self.assertIn("A login form", result["output"])

    def test_no_vision_provider_keeps_honest_note(self):
        a = make_agent(FakeClient([]))
        a._providers = [NV]
        self.assertFalse(a._describe_with_vision_model({"output": "x", "image_path": self.img}))


if __name__ == "__main__":
    unittest.main()
