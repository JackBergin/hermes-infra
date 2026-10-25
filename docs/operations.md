# Operations runbook

## Basic setup

This project assumes one gateway deployment per state directory. Requirements:

- Docker Engine with Docker Compose v2 (Linux or Docker Desktop), working Docker permissions.
- Python 3.10+, Git, and available disk space for growing sessions/logs/archives.
- Optional host Restic executable and a configured, independent object-storage backup repository.

From a clean checkout, run `make init`, edit `.env` to choose the Hermes image and state path, and run `make setup`. The setup wizard writes API tokens under the mounted `state/.env`. Run `make configure` to register the Git-managed read-only skills path and enable skill write approval. Then `make doctor` and `make up`.

For deterministic image selection, pin a published Hermes image digest:
```
HERMES_IMAGE=nousresearch/hermes-agent@sha256:<actual-digest>
```
The default stable tag in `.env.example` is **not immutable**. Verify tags and digests with Docker tooling and compare against upstream release notes before promoting one. Do not edit `state/.env` in CI or commit it.

## Operations

| Operation | Command | Important notes |
| --- | --- | --- |
| Start | `make up` | One active gateway writer per data directory |
| Stop | `make down` | Stops gateway; preserves state |
| Logs | `make logs` | Logs can contain sensitive data |
| Check Compose and local skills | `make doctor` | Reports image pin, mount, skill collisions |
| Re-run config approvals | `make down && make configure` | Mutates live Hermes config via official CLI |
| Dashboard login | `make dashboard-auth` | Enables the loopback dashboard; rerun to rotate the password and sign out sessions |
| Back up | `make backup` | Verified local ZIP only |
| Offsite backup | `make backup-offsite` | Requires Restic, secret configuration |
| Test restoration | `make restore-test FILE=backups/<archive>.zip` | Isolated import + config path probe |
| Start fresh | `make reset` | Verified backup, stops the gateway, moves `state/` to `state.old-<UTC time>/`; never deletes |
| Upgrade | `make upgrade IMAGE=<digest-pinned-image>` | Makes local pre-upgrade backup; migrations may be irreversible |

## Working with skills

Approved skill folders live in `skills/`. Their directory name must be unique across local state skills and the approved repo: otherwise Hermes may resolve a local one first. `make doctor` looks for duplicate directory names with `SKILL.md` markers. The Git checkout is read-only inside the gateway, and any agent-created skills live in its mutable state by default. Review diffs and promote deliberately into `skills/`; do not blindly sync runtime-created skills back into Git.

Upstream skill precedence and write approval are documented in the [skills guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/).

## Network posture

Only the web dashboard is published, on host `127.0.0.1:9119` (change the host port with `HERMES_DASHBOARD_HOST_PORT`), and it stays off until `make dashboard-auth` sets a login. Gateway and API ports are not published. Hermes connects outbound to chat platforms. To reach the dashboard from another machine, use an SSH tunnel or Tailscale instead of changing the binding; see [Dashboard exposure](security.md#dashboard-exposure). If you enable the API server, set its key, keep it on loopback or behind a secured reverse proxy, and restrict ingress in the host firewall. Do not set open CORS origins on a public service.

The default Compose stack does **not** expose the Docker socket or mount the host filesystem except the declared state and skill directories. Containers are not a complete sandbox: limit agent tools, MCP credentials, and host-level permissions according to your threat model.

## Observability and upgrades

The Docker container can be "running" while agent tasks fail. Check logs, provider reachability, rate limits, skill collisions, and repeated tool failures. Track response quality independently using a representative task suite under controlled prompts/models. GitHub CI here checks syntax, Python units and Compose shape, **not** semantic quality.

Before an upgrade, review upstream changes, obtain a tested backup, then run `make upgrade IMAGE=...`. This updates the image reference and recreates the container. If a migration breaks state, use the pre-upgrade image and pre-upgrade archive together. Do not run old and new gateways concurrently against the same data directory.

For multi-instance deployments, allocate an **independent** persistent home for each instance; do not mount one home into multiple concurrently running gateways. This template is intentionally single instance rather than unsupported active-active.

## Behavioral checks

Use `make eval` on an isolated test deployment to run opt-in, provider-billed output checks. These are not exhaustive semantic quality tests; see [Evaluations](evaluations.md).
