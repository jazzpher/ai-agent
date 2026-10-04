"""Work log, file cards, quick-choice chips, files tree. No network."""
import os, tempfile, unittest, zipfile
import worklog, files_tree

RAW = ("Sige.\n\n🔧 **Action:** `web_search`, `write_file`\n\n"
       "✅ `web_search` — 1.20s — 5 results\n"
       "✅ `write_file` — 0.01s — wrote a.json\n[[file:a.json]]\n\n---\n\nTapos na.")


class WorklogTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        open(os.path.join(self.d, "a.json"), "w").write("\n".join(f'"k{i}": {i}' for i in range(30)))

    def test_groups_timings_and_card(self):
        out = worklog.render(RAW, self.d)
        self.assertIn("Explored <b>1</b>", out)
        self.assertIn("Edited files <b>1</b>", out)
        self.assertIn("1.2s", out); self.assertIn("10ms", out)
        self.assertIn('class="fc"', out); self.assertIn("+16 more lines", out)
        self.assertNotIn("[[file:", out); self.assertNotIn("🔧", out)
        self.assertIn("Tapos na.", out)

    def test_consecutive_blocks_all_render(self):
        raw = ("🔧 **Action:** `web_search`\n\n✅ `web_search` — 1.00s — ok\n\n---\n\n"
               "🔧 **Action:** `write_file`\n\n✅ `write_file` — 0.01s — ok\n\n---\n\n"
               "🔧 **Action:** `run_bash`\n\n✅ `run_bash` — 0.20s — ok\n\n---\n\nDone")
        out = worklog.render(raw, self.d)
        self.assertEqual(out.count('class="wl"'), 3); self.assertNotIn("🔧", out); self.assertNotIn("---", out)
        self.assertTrue(out.rstrip().endswith("Done"))

    def test_plain_text_untouched_and_idempotent(self):
        self.assertEqual(worklog.render("hello", self.d), "hello")
        once = worklog.render(RAW, self.d)
        self.assertEqual(worklog.render(once, self.d), once)

    def test_running_block_and_errors(self):
        out = worklog.render("🔧 **Action:** `run_bash`\n\n", self.d)
        self.assertIn("Working", out)
        bad = worklog.render("🔧 **Action:** `run_bash`\n\n❌ `run_bash` — 0.50s — error — <b>x</b>\n", self.d)
        self.assertIn("issue", bad); self.assertIn("&lt;b&gt;x", bad)

    def test_ask_chips_escape_and_limit(self):
        out = worklog.render("[[ask:Para saan?|Etsy|<i>x</i>|c|d|e]]", self.d)
        self.assertEqual(out.count("class=\"qc\""), 4); self.assertNotIn("<i>x", out)

    def test_binary_card_has_no_preview(self):
        open(os.path.join(self.d, "g.pdf"), "wb").write(b"%PDF")
        self.assertIn("fc-bin", worklog.file_card(os.path.join(self.d, "g.pdf"), "g.pdf"))

    def test_summary(self):
        self.assertIn("`a.json`", worklog.files_summary(["a.json"], self.d))
        self.assertEqual(worklog.files_summary([], self.d), "")


class FilesTreeTest(unittest.TestCase):
    def test_tree_zip_usage(self):
        d = tempfile.mkdtemp(); os.makedirs(os.path.join(d, "sub"))
        open(os.path.join(d, "sub", "r.py"), "w").write("x=1"); open(os.path.join(d, "n.pdf"), "wb").write(b"%PDF")
        open(os.path.join(d, ".hidden"), "w").write("s")
        tree = files_tree.tree_html(d, "sub/r.py")
        self.assertIn("ft-dir", tree); self.assertIn("sel", tree); self.assertNotIn("hidden", tree)
        self.assertIn("2 files", files_tree.usage_text(d))
        with zipfile.ZipFile(files_tree.make_zip(d)) as z:
            self.assertEqual(sorted(z.namelist()), ["n.pdf", "sub/r.py"])
        self.assertIn("Wala pang file", files_tree.tree_html(tempfile.mkdtemp()))


class ModelPickerTest(unittest.TestCase):
    def test_choices_and_override_survive_refresh(self):
        import app
        from agent import AIAgent
        ids = [v for _, v in app.model_choices()]
        self.assertIn("deepseek-ai/deepseek-v4.1-flash", ids)
        a = AIAgent(api_key="k")
        app.set_model_choice("deepseek-ai/deepseek-v4.1-flash", a)
        a.model = "other"; app.apply_model_choice(a)
        self.assertEqual(a.model, "deepseek-ai/deepseek-v4.1-flash")
        app.set_model_choice("", a); a.model = "x"; app.apply_model_choice(a)
        self.assertEqual(a.model, "x")


if __name__ == "__main__":
    unittest.main()


class AgentLoopMarkersTest(unittest.TestCase):
    def tearDown(self):
        from unittest.mock import patch
        patch.stopall()

    def test_ask_user_ends_turn_with_choices(self):
        from test_agent_loop import FakeClient, make_agent, run
        client = FakeClient([("tool", "ask_user", {"question": "Para saan?", "options": ["Etsy", "Personal"]})])
        out = run(make_agent(client), "i-edit mo ito")
        self.assertIn("[[ask:Para saan?|Etsy|Personal]]", out)
        self.assertEqual(len(client.script), 0)  # no second model call: the turn stopped

    def test_new_file_gets_marker(self):
        import agent as agent_mod
        from unittest.mock import patch
        from test_agent_loop import FakeClient, make_agent, run
        d = tempfile.mkdtemp()
        with patch.object(agent_mod, "WORKSPACE_DIR", d):
            client = FakeClient([("tool", "write_file", {"path": "n.txt", "content": "hi"}), ("text", "ok")])
            a = make_agent(client)
            with patch.dict(agent_mod.TOOL_FUNCTIONS, {"write_file": lambda **k: (open(os.path.join(d, "n.txt"), "w").write("hi"), {"status": "success", "output": "ok"})[1]}):
                out = run(a, "gawa")
        self.assertIn("[[file:n.txt]]", out)
        self.assertIn("Mga file na ginawa", out)
