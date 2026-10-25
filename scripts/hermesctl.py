#!/usr/bin/env python3
"""Small Docker Compose lifecycle CLI for a single Hermes gateway deployment.

Only Python standard library is required. No cloud credentials are stored here.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path
import re
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
    print("Published API ports: none in the supplied Compose file.")
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
    print(f"Verified local backup: {path}")
    if offsite:
        if not os.environ.get("RESTIC_REPOSITORY"):
            raise InfraError("RESTIC_REPOSITORY is required for offsite backups.")
        if not shutil.which("restic"):
            raise InfraError("restic is not installed on the host.")
        run("restic", "backup", str(path), "--tag", "hermes-infra")
        print("Encrypted off-site backup sent to Restic repository.")
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
    for action in ("help", "init", "setup", "configure", "up", "down",
                   "restart", "logs", "doctor", "backup", "restore",
                   "restore-test", "upgrade"):
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
    elif args.command == "upgrade":
        upgrade(args.image)


if __name__ == "__main__":
    try:
        main()
    except (InfraError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
