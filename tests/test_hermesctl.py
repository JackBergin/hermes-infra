"""Pure-unit tests, no Docker daemon or Hermes secrets required."""
import base64
import hashlib
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


class DashboardTests(unittest.TestCase):
    def test_password_hash_matches_hermes_format(self):
        encoded = module.hash_password("correct horse battery")
        scheme, n, r, p, salt_b64, dk_b64 = encoded.split("$")
        self.assertEqual((scheme, n, r, p), ("scrypt", "16384", "8", "1"))
        expected = hashlib.scrypt(b"correct horse battery", salt=base64.b64decode(salt_b64),
                                  n=16384, r=8, p=1, dklen=32)
        self.assertEqual(base64.b64decode(dk_b64), expected)
        self.assertNotEqual(encoded, module.hash_password("correct horse battery"))

    def test_update_env_file(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Path(directory) / ".env"
            f.write_text("# keep\nOPENAI_API_KEY=sk-test\n"
                         "export HERMES_DASHBOARD_BASIC_AUTH_PASSWORD=plain\n"
                         "HERMES_DASHBOARD_BASIC_AUTH_USERNAME=old\n"
                         "HERMES_DASHBOARD_BASIC_AUTH_USERNAME=duplicate\n")
            module.update_env_file(f, {
                "HERMES_DASHBOARD_BASIC_AUTH_USERNAME": "admin",
                "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD": None,
                "HERMES_DASHBOARD_BASIC_AUTH_SECRET": "s3cret",
            })
            self.assertEqual(f.read_text(),
                             "# keep\nOPENAI_API_KEY=sk-test\n"
                             "HERMES_DASHBOARD_BASIC_AUTH_USERNAME=admin\n"
                             "HERMES_DASHBOARD_BASIC_AUTH_SECRET=s3cret\n")
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)

            created = Path(directory) / "new.env"
            module.update_env_file(created, {"HERMES_DASHBOARD": "1", "UNSET": None})
            self.assertEqual(created.read_text(), "HERMES_DASHBOARD=1\n")


if __name__ == "__main__":
    unittest.main()
