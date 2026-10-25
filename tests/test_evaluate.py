"""Behavioral harness unit tests, without invoking an LLM or Docker."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("evaluate", ROOT / "scripts" / "evaluate.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MatchAnswerTests(unittest.TestCase):
    def test_exact(self):
        self.assertTrue(module.match_answer({"expected_exact": "READY"}, "READY\n"))
        self.assertFalse(module.match_answer({"expected_exact": "READY"}, "not READY"))

    def test_contains(self):
        self.assertTrue(module.match_answer(
            {"expected_contains": ["first", "second"]}, "first and second"))
        self.assertFalse(module.match_answer(
            {"expected_contains": ["first", "second"]}, "first only"))

    def test_json(self):
        self.assertTrue(module.match_answer(
            {"expected_json": {"status": "ok"}}, '{"status": "ok"}'))
        self.assertFalse(module.match_answer(
            {"expected_json": {"status": "ok"}}, "not-json"))


if __name__ == "__main__":
    unittest.main()
