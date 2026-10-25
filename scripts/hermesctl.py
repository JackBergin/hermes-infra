#!/usr/bin/env python3
"""Small Docker Compose lifecycle CLI for a single Hermes gateway deployment.

Only Python standard library is required. No cloud credentials are stored here.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"
SKILLS = ROOT / "skills"
BACKUPS = ROOT / "backups"
DIGEST = re.compile(r"^nousresearch/hermes-agent@sha256:[0-9a-f]{64}$")
ENV_KEY = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
# Values the image's s6 dashboard run script treats as enabled.
DASHBOARD_ON = frozenset({"1", "true", "TRUE", "True", "yes", "YES", "Yes"})
DASHBOARD_LOGIN = ("HERMES_DASHBOARD_BASIC_AUTH_USERNAME",
                   "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH")
DASHBOARD_USER = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")


class InfraError(RuntimeError):
    pass


def run(*args: str, env: dict[str, str] | None = None, capture: bool = False) -> str:
    cmd = list(args)
    try:
        result = subprocess.run(cmd, cwd=ROOT, env=env, check=True,
                                text=True, stdout=subprocess.PIPE if capture else None)
        return result.stdout.strip() if capture else ""
    except FileNotFoundError as exc:
        raise InfraError(f"Command not installed: {cmd[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise InfraError(f"Command failed (exit {exc.returncode}): {' '.join(cmd[:5])}") from exc


def env_values(path: Path = CONFIG) -> dict[str, str]:
    """Parse this project's deliberately simple, non-secret .env example."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep and re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            values[key] = value.strip().strip('"').strip("'")
    return values


def require_config() -> dict[str, str]:
    if not CONFIG.is_file():
        raise InfraError("Missing .env: run 'make init' first.")
    values = env_values()
    if not values.get("HERMES_IMAGE"):
        raise InfraError("HERMES_IMAGE must be set in .env.")
    return values


def state_path(values: dict[str, str] | None = None) -> Path:
    values = values or require_config()
    raw = values.get("HERMES_STATE_DIR", "./state")
    path = Path(raw).expanduser()
    return (path if path.is_absolute() else ROOT / path).resolve()


def compose(*args: str, env: dict[str, str] | None = None, capture: bool = False) -> str:
    return run("docker", "compose", *args, env=env, capture=capture)


def is_running() -> bool:
    return bool(compose("ps", "--status", "running", "--quiet", "hermes", capture=True))


def ensure_stopped() -> None:
    if is_running():
        raise InfraError("Gateway is running. Run 'make down' first to avoid concurrent writers.")


def configure() -> None:
    require_config()
    ensure_stopped()
    compose("run", "--rm", "--no-deps", "hermes", "config", "set",
            "skills.external_dirs", '["/opt/company-skills"]')
    compose("run", "--rm", "--no-deps", "hermes", "config", "set",
            "skills.write_approval", "true")
    print("Configured external read-only skills and staged skill writes.")


def skill_names(directory: Path) -> set[str]:
    if not directory.exists():
        return set()
    return {entry.name for entry in directory.iterdir()
            if entry.is_dir() and (entry / "SKILL.md").is_file()}


def check_zip(archive: Path) -> None:
    if not archive.is_file():
        raise InfraError(f"Missing archive: {archive}")
    try:
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            if not names:
                raise InfraError("Backup archive is empty.")
            bad = z.testzip()
            if bad:
                raise InfraError(f"Corrupt archive member: {bad}")
    except (zipfile.BadZipFile, OSError, EOFError) as exc:
        raise InfraError(f"Archive is not a valid readable ZIP: {archive}") from exc


def setup() -> None:
    require_config()
    ensure_stopped()
    compose("run", "--rm", "--no-deps", "hermes", "setup")
    print("Hermes setup finished. Next run 'make configure', then 'make up'.")


def update_env_file(path: Path, updates: dict[str, str | None]) -> None:
    """Set keys in a dotenv file (None removes them), keeping every other line."""
    pending = dict(updates)
    existing = path.read_text().splitlines() if path.exists() else []
    lines = []
    for line in existing:
        match = ENV_KEY.match(line)
        if match and match.group(1) in updates:
            value = pending.pop(match.group(1), None)
            if value is not None:
                lines.append(f"{match.group(1)}={value}")
            continue
        lines.append(line)
    lines += [f"{key}={value}" for key, value in pending.items() if value is not None]
    # Atomic 0600 replace so Hermes never reads a half-written credentials file.
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write("\n".join(lines) + "\n")
        if path.exists():
            # Keep the owner (e.g. container UID 10000) so Hermes can still read it.
            with contextlib.suppress(OSError):
                os.chown(temp, path.stat().st_uid, path.stat().st_gid)
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)
        raise


def hash_password(password: str) -> str:
    """Same format and parameters as Hermes' plugins.dashboard_auth.basic.hash_password."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32, maxmem=0)
    return f"scrypt$16384$8$1${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def dashboard_url(values: dict[str, str]) -> str:
    return f"http://127.0.0.1:{values.get('HERMES_DASHBOARD_HOST_PORT') or '9119'}"


def dashboard_auth() -> None:
    values = require_config()
    state = state_path(values)
    if not state.is_dir():
        raise InfraError("Missing state directory: run 'make init' and 'make setup' first.")
    enabled = values.get("HERMES_DASHBOARD", "") in DASHBOARD_ON
    # Only an already-enabled running container misses the change on 'make up'.
    apply = "make restart" if enabled and is_running() else "make up"
    username = input("Dashboard username [admin]: ").strip() or "admin"
    if not DASHBOARD_USER.fullmatch(username):
        raise InfraError("Username must be 1-64 letters, digits, or . _ @ -")
    password = getpass.getpass("Dashboard password (12+ characters): ")
    if len(password) < 12:
        raise InfraError("Password must be at least 12 characters.")
    if getpass.getpass("Repeat password: ") != password:
        raise InfraError("Passwords do not match.")
    credentials = state / ".env"
    try:
        update_env_file(credentials, {
            "HERMES_DASHBOARD_BASIC_AUTH_USERNAME": username,
            "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH": hash_password(password),
            # Hermes prefers a plaintext env password over the hash.
            "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD": None,
            # A new token-signing key signs out every existing dashboard session.
            "HERMES_DASHBOARD_BASIC_AUTH_SECRET": base64.b64encode(secrets.token_bytes(32)).decode(),
        })
    except PermissionError as exc:
        raise InfraError(f"Cannot write {credentials}. Map HERMES_UID/HERMES_GID to your "
                         "host user (docs/security.md) instead of loosening permissions.") from exc
    if not enabled:
        update_env_file(CONFIG, {"HERMES_DASHBOARD": "1"})
    print(f"Dashboard login saved to {credentials} (scrypt hash; existing sessions revoked).")
    print(f"Apply it with '{apply}', then open {dashboard_url(values)}")


def doctor() -> None:
    values = require_config()
    state = state_path(values)
    print("Compose configuration:")
    compose("config", "-q")
    print("  Valid")
    print(f"Image: {values['HERMES_IMAGE']}")
    if not DIGEST.fullmatch(values["HERMES_IMAGE"]):
        print("  WARNING: Use an immutable image digest for production.")
    print(f"Persistent state: {state}")
    print(f"  Exists: {state.is_dir()}")
    print(f"  Credentials file present: {(state / '.env').is_file()}")
    approved = skill_names(SKILLS)
    local = skill_names(state / "skills") if (state / "skills").is_dir() else set()
    overlap = approved & local
    print(f"Approved skills: {len(approved)}; local skills: {len(local)}")
    if overlap:
        print("  WARNING: Local skills shadow approved skills: " + ", ".join(sorted(overlap)))
    print("Published ports: dashboard only, on host loopback; gateway/API ports are not published.")
    if values.get("HERMES_DASHBOARD", "") in DASHBOARD_ON:
        print(f"Dashboard: enabled at {dashboard_url(values)}")
        try:
            login = env_values(state / ".env")
        except PermissionError:
            login = None
            print("  Cannot read state/.env to check the dashboard login.")
        if login is not None and not all(login.get(key) for key in DASHBOARD_LOGIN):
            print("  WARNING: No dashboard login in state/.env, so Hermes will refuse to "
                  "start it. Run 'make dashboard-auth'.")
        if login and login.get("HERMES_DASHBOARD_BASIC_AUTH_PASSWORD"):
            print("  WARNING: A plaintext dashboard password in state/.env overrides the "
                  "hash. Run 'make dashboard-auth' to replace it.")
    else:
        print("Dashboard: disabled. Run 'make dashboard-auth' to set a login and enable it.")
    print("Backup target: local only unless RESTIC_REPOSITORY configured for --offsite.")
    if state.exists() and not state.is_dir():
        raise InfraError("Persistent state path is not a directory.")


def backup(offsite: bool = False) -> Path:
    require_config()
    BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"hermes-{stamp}.zip"
    path = BACKUPS / name
    container_path = "/opt/data/backups/" + name
    if path.exists():
        raise InfraError(f"Backup already exists: {path}")
    # Hermes backup uses the SQLite online backup API; never simply tar live WAL files.
    if is_running():
        compose("exec", "-T", "hermes", "hermes", "backup", "--output", container_path, "--keep", "0")
    else:
        compose("run", "--rm", "--no-deps", "hermes", "backup",
                "--output", container_path, "--keep", "0")
    # docker compose cp avoids UID-10000 bind-mount ownership issues on the host.
    # When stopped, create (but do not start) the service container so cp has a source.
    if not is_running():
        compose("create", "--no-recreate", "hermes")
    compose("cp", f"hermes:{container_path}", str(path))
    os.chmod(path, 0o600)
    check_zip(path)
    # Record provenance so recovery can select the matching code and image.
    try:
        revision = run("git", "rev-parse", "HEAD", capture=True)
    except InfraError:
        revision = "unavailable"
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    manifest = path.with_suffix(".manifest.json")
    manifest.write_text(json.dumps({
        "archive": path.name,
        "sha256": digest.hexdigest(),
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "git_revision": revision,
        "hermes_image": env_values().get("HERMES_IMAGE", ""),
        "state_directory": str(state_path()),
        "archive_size_bytes": path.stat().st_size,
    }, indent=2) + "\n")
    os.chmod(manifest, 0o600)
    print(f"Verified local backup: {path}")
    print(f"Recovery manifest: {manifest}")
    if offsite:
        if not os.environ.get("RESTIC_REPOSITORY"):
            raise InfraError("RESTIC_REPOSITORY is required for offsite backups.")
        if not shutil.which("restic"):
            raise InfraError("restic is not installed on the host.")
        run("restic", "backup", str(path), str(manifest), "--tag", "hermes-infra")
        print("Encrypted off-site backup and manifest sent to Restic repository.")
    else:
        print("WARNING: This is a LOCAL backup only. Use 'make backup-offsite' for disaster recovery.")
    return path


def restore(archive: Path, force: bool = False, smoke: bool = False) -> None:
    require_config()
    check_zip(archive)
    if smoke:
        with tempfile.TemporaryDirectory(prefix="hermes-restore-test-") as temp:
            target = Path(temp, "state")
            target.mkdir(mode=0o700)
            custom_env = os.environ.copy()
            custom_env["HERMES_STATE_DIR"] = str(target)
            # Keep the temporary bind mount owned by the invoking host user so
            # TemporaryDirectory can clean it up after the isolated smoke test.
            if hasattr(os, "getuid") and hasattr(os, "getgid"):
                custom_env["HERMES_UID"] = str(os.getuid())
                custom_env["HERMES_GID"] = str(os.getgid())
            compose("run", "--rm", "--no-deps", "-v",
                    f"{archive}:/tmp/hermes-restore.zip:ro", "hermes",
                    "import", "/tmp/hermes-restore.zip", "--force", env=custom_env)
            compose("run", "--rm", "--no-deps", "hermes", "config",
                    "path", env=custom_env)
            print("Isolated restore and configuration smoke test passed.")
            print("NOTE: This does not test credentials, live tools, or response quality.")
        return

    ensure_stopped()
    target = state_path()
    if target.exists() and any(target.iterdir()) and not force:
        raise InfraError("State directory is not empty. Import overwrites matching files "
                         "but can leave others. Use restore-force only after preserving "
                         "the old state and understanding the risk.")
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    compose("run", "--rm", "--no-deps", "-v",
            f"{archive}:/tmp/hermes-restore.zip:ro", "hermes",
            "import", "/tmp/hermes-restore.zip", "--force")
    print("Hermes archive imported. Validate configuration before 'make up'.")


def set_aside(state: Path, stamp: str) -> Path:
    """Rename *state* to a timestamped sibling. Never deletes or overwrites anything."""
    aside = state.with_name(f"{state.name}.old-{stamp}")
    if aside.exists():
        raise InfraError(f"{aside} already exists; refusing to overwrite it.")
    state.rename(aside)
    return aside


def reset() -> None:
    values = require_config()
    state = state_path(values)
    if not state.is_dir() or not any(state.iterdir()):
        raise InfraError(f"Nothing to reset: {state} is missing or empty.")
    print(f"Reset takes a verified backup, stops the gateway, and moves {state} aside.\n"
          "Nothing is deleted, but Hermes starts over with empty state: setup, "
          "credentials, sessions and memories.")
    if input("Type 'reset' to continue: ").strip() != "reset":
        raise InfraError("Reset cancelled; nothing was changed.")
    # Backup comes first so a failure leaves the deployment exactly as it was.
    archive = backup(offsite=False)
    compose("down")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        aside = set_aside(state, stamp)
    except OSError as exc:
        raise InfraError(f"Could not move {state} aside ({exc}). The gateway is stopped "
                         "and its state is untouched; run 'make up' to resume.") from exc
    state.mkdir(mode=0o700)
    print(f"Previous state kept at {aside} (Git-ignored; it still holds credentials).")
    print(f"Verified backup: {archive}")
    steps = ["make setup", "make configure"]
    if values.get("HERMES_DASHBOARD", "") in DASHBOARD_ON:
        steps.append("make dashboard-auth")
    print("Next: " + ", ".join(f"'{step}'" for step in steps) + ", then 'make up'.")


def upgrade(image: str) -> None:
    require_config()
    if not DIGEST.fullmatch(image):
        raise InfraError("Upgrade image must be nousresearch/hermes-agent@sha256:<64 lowercase hex>.")
    previous = CONFIG.read_text()
    current = env_values().get("HERMES_IMAGE")
    if current == image:
        raise InfraError("That image is already selected.")
    run("docker", "pull", image)
    # Always capture a recovery point before touching a running deployment.
    archive = backup(offsite=False)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    old_file = BACKUPS / f"compose-env-pre-upgrade-{stamp}.txt"
    old_file.write_text(previous)
    os.chmod(old_file, 0o600)
    new_lines = []
    for line in previous.splitlines():
        new_lines.append(f"HERMES_IMAGE={image}" if line.startswith("HERMES_IMAGE=") else line)
    CONFIG.write_text("\n".join(new_lines) + "\n")
    print(f"Pre-upgrade archive: {archive}")
    print(f"Previous image: {current}; previous Compose settings: {old_file}")
    try:
        compose("up", "-d")
    except InfraError:
        print("WARNING: Upgrade failed. Do not just downgrade the image; a schema "
              "migration may have changed persisted state. See disaster recovery docs.", file=sys.stderr)
        raise
    print("Container recreated. Verify logs, integrations, and response quality manually.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    for action in ("help", "init", "setup", "configure", "dashboard-auth", "up",
                   "down", "restart", "logs", "doctor", "backup", "restore",
                   "restore-test", "reset", "upgrade"):
        p = sub.add_parser(action)
        if action == "backup":
            p.add_argument("--offsite", action="store_true")
        if action in ("restore", "restore-test"):
            p.add_argument("archive", type=Path)
            if action == "restore":
                p.add_argument("--force", action="store_true")
        if action == "upgrade":
            p.add_argument("image")
    args = parser.parse_args()
    if args.command in (None, "help"):
        parser.print_help()
        return
    if args.command == "init":
        if CONFIG.exists():
            print(".env already exists; preserving it.")
        else:
            shutil.copyfile(EXAMPLE, CONFIG)
            os.chmod(CONFIG, 0o600)
            print("Created .env from .env.example.")
        state_path().mkdir(mode=0o700, parents=True, exist_ok=True)
        BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
        SKILLS.mkdir(parents=True, exist_ok=True)
        print("Next: review .env, run 'make setup', 'make configure', then 'make up'.")
    elif args.command == "setup":
        setup()
    elif args.command == "configure":
        configure()
    elif args.command == "dashboard-auth":
        dashboard_auth()
    elif args.command == "up":
        require_config()
        compose("up", "-d")
    elif args.command == "down":
        require_config()
        compose("down")
    elif args.command == "restart":
        require_config()
        compose("restart")
    elif args.command == "logs":
        require_config()
        compose("logs", "--tail", "150", "-f")
    elif args.command == "doctor":
        doctor()
    elif args.command == "backup":
        backup(offsite=args.offsite)
    elif args.command in ("restore", "restore-test"):
        restore(args.archive.expanduser().resolve(),
                force=getattr(args, "force", False),
                smoke=(args.command == "restore-test"))
    elif args.command == "reset":
        reset()
    elif args.command == "upgrade":
        upgrade(args.image)


if __name__ == "__main__":
    try:
        main()
    except (InfraError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
