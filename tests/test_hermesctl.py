"""Pure-unit tests, no Docker daemon or Hermes secrets required."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hermesctl", ROOT / "scripts" / "hermesctl.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ConfigTests(unittest.TestCase):
    def test_parse_env(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Path(directory) / ".env"
            f.write_text("# comment\nHERMES_IMAGE=nousresearch/hermes-agent:stable\nX=abc\n")
            self.assertEqual(module.env_values(f)["X"], "abc")

    def test_digest_pin_validation(self):
        self.assertTrue(module.DIGEST.fullmatch(
            "nousresearch/hermes-agent@sha256:" + "a" * 64))
        self.assertFalse(module.DIGEST.fullmatch("nousresearch/hermes-agent:latest"))

    def test_skill_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            (p / "approved").mkdir()
            (p / "approved" / "SKILL.md").write_text("# Skill")
            (p / "ignored").mkdir()
            self.assertEqual(module.skill_names(p), {"approved"})

    def test_zip_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "good.zip"
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("config.yaml", "skills: {}\n")
            module.check_zip(p)
            p.write_bytes(b"corrupt")
            with self.assertRaises(module.InfraError):
                module.check_zip(p)


if __name__ == "__main__":
    unittest.main()
