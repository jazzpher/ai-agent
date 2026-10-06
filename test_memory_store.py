"""Memory store: database first, file fallback. Uses a throwaway local Postgres (pgserver) if present."""
import os
import tempfile
import unittest
from unittest.mock import patch

import agentic
import memory_store

try:
    import pgserver
except ImportError:  # Render does not need it
    pgserver = None


class FallbackTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "MEMORY.md")

    def test_no_database_uses_file(self):
        with patch.dict(os.environ, {"DATABASE_URL": "", "AGENT_DATABASE_URL": ""}):
            self.assertFalse(memory_store.enabled())
            agentic.memory_action(self.path, "add", "uses file")
            self.assertIn("uses file", agentic.memory_action(self.path, "list")["output"])

    def test_unreachable_database_falls_back_to_file(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://u:p@127.0.0.1:1/x"}):
            self.assertIsNone(memory_store.db_read())
            r = agentic.memory_action(self.path, "add", "still saved")
            self.assertEqual(r["status"], "success")
            self.assertIn("still saved", agentic.memory_action(self.path, "list")["output"])
            self.assertIn("unreachable", memory_store.status())


@unittest.skipIf(pgserver is None, "pgserver not installed")
class DatabaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = pgserver.get_server(tempfile.mkdtemp())
        cls.url = cls.srv.get_uri()

    def setUp(self):
        memory_store._ready = False
        self.env = patch.dict(os.environ, {"DATABASE_URL": self.url})
        self.env.start()
        memory_store.db_write([])
        self.path = os.path.join(tempfile.mkdtemp(), "MEMORY.md")

    def tearDown(self):
        self.env.stop()

    def test_survives_restart(self):
        agentic.memory_action(self.path, "add", "Prefers Taglish")
        os.remove(self.path)  # Render restart wipes the disk
        self.assertIn("Prefers Taglish", agentic.memory_action(self.path, "list")["output"])
        self.assertTrue(os.path.exists(self.path))

    def test_forget_and_dedupe(self):
        agentic.memory_action(self.path, "add", "a fact")
        self.assertIn("Already", agentic.memory_action(self.path, "add", "A FACT")["output"])
        self.assertIn("Forgot 1", agentic.memory_action(self.path, "forget", "fact")["output"])
        if os.path.exists(self.path):
            os.remove(self.path)
        self.assertIn("empty", agentic.memory_action(self.path, "list")["output"])

    def test_seeds_empty_database_from_file(self):
        agentic._write_file(self.path, ["- old local fact"])
        self.assertIn("old local fact", agentic.memory_action(self.path, "list")["output"])
        self.assertEqual(memory_store.db_read(), ["- old local fact"])

    def test_secrets_never_reach_database(self):
        r = agentic.memory_action(self.path, "add", "token nvapi-abcdefghijkl123")
        self.assertEqual(r["status"], "error")
        self.assertEqual(memory_store.db_read(), [])

    def test_special_characters(self):
        agentic.memory_action(self.path, "add", "O'Brien; DROP TABLE agent_memory; -- \"x\" ñ 日本")
        os.remove(self.path)
        self.assertIn("O'Brien", agentic.memory_action(self.path, "list")["output"])
        self.assertEqual(len(memory_store.db_read()), 1)

    def test_agent_prompt_loads_from_database(self):
        import agent as agent_mod
        agentic.memory_action(agent_mod.MEMORY_FILE, "add", "db-loaded-fact")
        try:
            os.remove(agent_mod.MEMORY_FILE)
            a = agent_mod.AIAgent(api_key="x")
            self.assertIn("db-loaded-fact", a.system_prompt)
        finally:
            agentic.memory_action(agent_mod.MEMORY_FILE, "forget", "db-loaded-fact")


if __name__ == "__main__":
    unittest.main()
