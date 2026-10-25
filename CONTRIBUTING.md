# Contributing

Pull requests welcome. Keep the core compatible with upstream Hermes rather than patching its runtime.

1. Open an issue for behavioral or security-sensitive changes.
2. Run `make check` (Python 3.10+) and `docker compose config --quiet` after copying `.env.example` to `.env`.
3. For deployment changes, demonstrate a clean setup and an isolated restore test.
4. Never commit `state/`, `backups/`, secrets, personal conversation history, or credential-bearing archives.
5. Pin new GitHub Actions to vetted releases and use least-privilege permissions.

Pull requests that change backups, credentials, import semantics, or network exposure should include a threat-model note.
