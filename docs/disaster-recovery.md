# Disaster recovery

## Recovery objectives

Set an explicit **RPO** (maximum data loss) and **RTO** (maximum outage) for your deployment. If you back up once per day, the practical worst-case RPO is close to 24 hours, even if every backup succeeds. Object storage should be in a distinct failure domain from the host.

This template uses the native Hermes ZIP backup command, which handles SQLite consistently even while the gateway is active. A successful `hermes backup` exits zero only if all chosen files were archived. An incomplete archive must not be promoted. ZIP CRC verification adds a second check but is **not** a substitute for real restore testing.

## Backup procedure

1. In the host, set `RESTIC_REPOSITORY` to an independent object-storage repository and provide Restic credentials through a secure source (`RESTIC_PASSWORD_FILE`, cloud-credential helper, etc.).
2. Initialize the repository once with `restic init`.
3. Run `make backup-offsite`. The operation writes a timestamped local Hermes ZIP, copies it out of the container using the Docker API, checks CRC, then uploads it through Restic.
4. Configure a scheduler such as the sample user systemd timer in `deploy/systemd/`. Check failed jobs. Choose retention carefully, e.g. `restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --prune` only after verifying backup isolation and repository access.
5. Periodically obtain a selected historical archive with `restic restore <snapshot-id> --target <destination>` and use `make restore-test FILE=<archive>`.

Restic secrets must be present in the environment where the command runs. The project deliberately does not copy cloud passwords into Compose or install a scheduler automatically. Encrypted offsite backups do not prevent an attacker with BOTH Restic repository-write access and encryption-key access from destroying recovery points: use provider-side immutable/versioned retention where possible.

## Fresh-server recovery

1. Provision a machine with compatible Docker Engine, Docker Compose, and Python.
2. Clone this repository at the Git commit used by the original deployment, and select the matching **Hermes image digest**. Make sure the new host is not sharing the old live data directory with another gateway.
3. Run `make init`, edit the new `.env` as appropriate, and stop any gateway (`make down`).
4. Recover a known-good ZIP from Restic to a secure host path. Verify it with `make restore-test FILE=/path/to/archive.zip`.
5. Ensure `state/` is empty; run `make restore FILE=/path/to/archive.zip`. Import may contain sensitive credentials and tokens, so review permissions after restore.
6. Run `make doctor`, then `make up`, then `make logs`. Test chat-channel authentication, models, skills and a representative set of agent tasks.
7. Rotate tokens if the lost server could have been compromised. Some browser-auth credentials may not be included in native Hermes backups and may require manual reauthentication.

**Do not** launch a gateway sharing the original live state path while you restore/test another writer. Use a fresh state path. The `restore-test` command builds an isolated temporary state directory; its success proves import and basic config inspection, not full agent behavior.

## Upgrade rollback

Hermes can migrate persisted configuration at boot. A correct rollback restores *both* the previous image **and** the ZIP captured before the upgrade. Simply reverting the Compose image may boot outdated code against a newer on-disk state.

The upgrade helper backs up locally. For disaster resilience, first run `make backup-offsite` and verify the remote snapshot. The helper also stores a copy of the previous root Compose `.env` in ignored `backups/`. Always exercise recovery on a separate host before depending on a rollback.

## Exclusions and honesty

Hermes backup excludes its source tree and prior backup archives. Some special credential stores are also excluded. Restore scripts cannot guarantee third-party API availability, unchanged model behavior, or token validity. Document these external dependencies in your operating runbook.
