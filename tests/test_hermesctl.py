"""Pure-unit tests, no Docker daemon or Hermes secrets required."""
import base64
import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock
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


class ResetTests(unittest.TestCase):
    """Docker is mocked: these check ordering and that state is never deleted."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.state = self.root / "state"
        self.state.mkdir()
        (self.state / "config.yaml").write_text("model: {}\n")
        self.calls = []
        values = {"HERMES_IMAGE": "nousresearch/hermes-agent:stable",
                  "HERMES_STATE_DIR": str(self.state)}

        def backup(offsite=False):
            self.calls.append("backup")
            return Path("backups/hermes-test.zip")

        def compose(*args, **kwargs):
            self.calls.append(" ".join(args))

        for name, fake in (("require_config", lambda: values),
                           ("backup", backup), ("compose", compose)):
            patcher = mock.patch.object(module, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def reset(self, answer):
        with mock.patch("builtins.input", return_value=answer), \
                contextlib.redirect_stdout(io.StringIO()):
            module.reset()

    def test_backs_up_then_stops_then_sets_state_aside(self):
        self.reset("reset")
        self.assertEqual(self.calls, ["backup", "down"])
        [aside] = self.root.glob("state.old-*")
        self.assertEqual((aside / "config.yaml").read_text(), "model: {}\n")
        self.assertEqual(list(self.state.iterdir()), [])
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)

    def test_cancel_or_failed_backup_changes_nothing(self):
        with self.assertRaises(module.InfraError):
            self.reset("no")

        def failing_backup(offsite=False):
            raise module.InfraError("backup failed")

        with mock.patch.object(module, "backup", failing_backup), \
                self.assertRaises(module.InfraError):
            self.reset("reset")
        self.assertEqual(self.calls, [])
        self.assertTrue((self.state / "config.yaml").is_file())
        self.assertEqual(list(self.root.glob("state.old-*")), [])

    def test_refuses_empty_state(self):
        (self.state / "config.yaml").unlink()
        with self.assertRaises(module.InfraError):
            self.reset("reset")
        self.assertEqual(self.calls, [])

    def test_set_aside_never_overwrites(self):
        aside = self.root / "state.old-20260101T000000Z"
        aside.mkdir()
        (aside / "keep").write_text("old")
        with self.assertRaises(module.InfraError):
            module.set_aside(self.state, "20260101T000000Z")
        self.assertEqual((aside / "keep").read_text(), "old")
        self.assertTrue((self.state / "config.yaml").is_file())


if __name__ == "__main__":
    unittest.main()
