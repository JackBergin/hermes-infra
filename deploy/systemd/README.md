# Example user-level systemd backup timer (Linux)

These unit files are **templates**; installing them is opt-in.

1. Install Restic on the host and create/init an offsite Restic repository.
2. Create `~/.config/hermes-infra/backup.env` with `RESTIC_REPOSITORY=...` and `RESTIC_PASSWORD_FILE=/absolute/path/to/mode-0600/password-file`. Secure both files and any cloud credentials. Do not commit them.
3. Copy the service/timer files into `~/.config/systemd/user/`.
4. **Edit `WorkingDirectory=` in the service** if this repository isn't at `~/hermes-infra`, and verify that `/usr/bin/make` exists on your Linux system.
5. Run `systemctl --user daemon-reload && systemctl --user enable --now hermes-infra-backup.timer`.
6. Exercise `systemctl --user start hermes-infra-backup.service` and inspect `journalctl --user -u hermes-infra-backup.service` for success. Restore-test a recent archive.

Timer is at 03:00 local machine time with up to 10 minutes jitter. User systemd services may not run while the user is logged out unless linger is enabled, e.g. `loginctl enable-linger "$USER"` (requires appropriate host policy).

The timer itself does not set backup retention or alerting. Configure those separately.
