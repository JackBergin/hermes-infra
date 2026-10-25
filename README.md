# Hermes Infra

**Reproducible, recoverable Docker infrastructure for [Hermes Agent](https://github.com/NousResearch/hermes-agent).**

An opinionated, community-maintained deployment starter. It is **not** a fork of Hermes and **not** affiliated with Nous Research.

## Quick start

Prerequisites: Linux/macOS with Docker Engine + Docker Compose v2, Python 3.10+, and Git.

```bash
git clone https://github.com/JackBergin/hermes-infra.git
cd hermes-infra
make init
# Review .env and optionally replace HERMES_IMAGE with a digest-pinned image.
make setup           # Interactive Hermes credentials/provider/channel setup
make configure       # Enable read-only repository skills and skill write approvals
make doctor
make up
make logs
```

Use `make down` to stop the gateway. The agent data is stored in `./state/`, not in the image. Hermes credentials live in `state/.env`, **not** in this repository's Compose `.env`.

The `skills/` directory is version-controlled and mounted read-only at `/opt/company-skills`. Local runtime skills can shadow external ones. `make doctor` detects same-named skills and warns.

### Backups and restores

```bash
make backup             # Verified local Hermes ZIP archive
make backup-offsite     # Local archive + encrypted Restic upload
make restore-test FILE=backups/hermes-YYYYMMDDTHHMMSSZ.zip
make down
make restore FILE=backups/hermes-YYYYMMDDTHHMMSSZ.zip # Requires empty state
```

Configure `RESTIC_REPOSITORY` and `RESTIC_PASSWORD_FILE` in your shell/secret manager, initialize your Restic repository once (`restic init`), and schedule `make backup-offsite`. **Do not commit backup credentials or plaintext archives.** Local archives are in `backups/`, which is Git-ignored. `make backup` does not make an off-site copy.

For upgrades, provide a **digest-pinned** image (example is a placeholder, not a real digest):

```bash
make upgrade IMAGE=nousresearch/hermes-agent@sha256:<64-hex-character-digest>
```

An upgrade takes a verified pre-upgrade backup and changes the image reference. Configuration migrations may be irreversible. Rollback requires restoring matching pre-upgrade **image and state**; it is not an automatic tag downgrade.

### Features

- Pinned-image support, persistent bind-mounted state, and no network ports published by default.
- Git-managed skills in a read-only mount; separate native Hermes state for agent-created content.
- Native Hermes SQLite-consistent ZIP backups and CRC verification.
- Optional encrypted off-site Restic storage.
- Isolated restoration smoke tests with an alternate state directory.
- A single `hermesctl` command interface, preflight diagnostics, GitHub Actions static checks and unit tests.
- No second gateway against the same state directory, and no Docker socket exposed to the agent.

### Scope and limitations

This is an **initial usable infrastructure baseline**, not a high-availability control plane. There is no production fleet orchestration, key management service, automatic disaster recovery across clouds, or behavior-quality evaluation harness. Restoration tests verify archive integrity and a clean isolated import/configuration probe, not chat quality. The project has not been tested against every upstream Hermes release or Docker platform. Review [Security](docs/security.md), [Operations](docs/operations.md), and [Disaster Recovery](docs/disaster-recovery.md) before production use.

Commands and their implementation live in [scripts/hermesctl.py](scripts/hermesctl.py). The official references are [Docker](https://hermes-agent.nousresearch.com/docs/user-guide/docker/), [Skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/), and [CLI backup/import](https://hermes-agent.nousresearch.com/docs/reference/cli-commands/).

Licensed MIT. Contributions welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).
