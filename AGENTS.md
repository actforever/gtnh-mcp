# Project guidance

## Current implementation

- Backups support ZIP (Stored/Deflate with CRC validation) and tar.gz. `WORLD_DIRECTORY` defaults to `World`; NAS uses `backups/`. New jobs persist both restored directory names, while legacy journals without them retain `Worlds` semantics.
- `request_undo_restore` creates a separately confirmed restore from a successful job's retained `previous` directories. `snapshots` fingerprints and copies the source without consuming it; the same state machine preserves the current world and handles failure rollback. Keep this tool registered in both MCP and the AstrBot bridge; confirmation remains command-only in the AstrBot bridge (direct authenticated MCP clients can confirm).

- Python 3.13 / uv. `src/gtnh_mcp` contains configuration, fixed Bearer key validation, RCON, MCP tools, archive validation, Docker control and the persistent restore worker.
- `astrbot_plugin` is the separately installed AstrBot bridge. Check QQ ACLs in the plugin and encode actual message identities as X-GTNH-Actor; never accept identity or role from model arguments. Confirmation is a command, not an LLM tool.
- Plugin settings contain mcp_url, auth_secret, allowed_groups and admin_users. Nonblank page values override GTNH_AUTH_SECRET, ALLOWED_GROUPS and ADMIN_USERS in the AstrBot environment. Backend configuration contains no QQ ACLs. Do not register the service a second time through AstrBot native MCP configuration.
- MCP and AstrBot use Linux Docker host networking. Only the restore helper mounts the Docker socket and server directories; MCP calls it through a Unix socket.
- The restore HTTP client uses an explicit `http://localhost/rpc` URL without `base_url`; its hostname is only HTTP metadata. The UDS transport selects the actual socket path. Test-only loopback transport overrides the full RPC URL.
- Details belong in `docs/architecture.md`, `docs/security.md`, `docs/restore.md`, `docs/configuration.md`, `docs/deployment.md`, and `docs/testing.md`.
- Reuse the user's existing AstrBot with its built-in QQ Official adapter. `compose.chat.yaml` is an optional AstrBot-only Linux host-network template for new installations; do not introduce a separate QQ gateway. Use actual `/gtnh_identity` output for platform/group/user ACLs, never assume numeric QQ identifiers. Keep every `.env.example` variable documented in `docs/configuration.md` and the end-to-end NAS procedure in `docs/deployment.md`.

## Development

- Install: `uv sync --locked --group dev`.
- Validate: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`.
- Optional simulated black-box test: `uv run pytest -m blackbox -q`; excluded by default. Its test-only TCP helper transport does not replace production Unix-socket deployment verification.
- Build images on NAS: `docker compose build`. Do not invoke local Docker (including config) or WSL. Real restore acceptance requires an isolated GTNH container and actual backup; never use production as the first test.
- Update this file when architecture, commands or constraints change. Update relevant docs alongside behavior/configuration changes. Record limitations honestly.

## Invariants

- No generic RCON tool, shell endpoint, arbitrary container selector, or caller-supplied filesystem path.
- Verify the fixed key on every backend call; group/admin ACLs and task visibility are enforced in the plugin. Never log tokens, passwords or secrets. Direct key holders have access to all MCP tools, including confirmation; missing actor metadata defaults to api-client.
- Bind confirmation to actor, group, archive digest and expiry. Persist before side effects. Never automatically retry RCON writes.
- Hold the shared operation lock through restoration; confirm exit before moving directories. Preserve old directories and stop on uncertain recovery state.
- Destructive behavior requires meaningful failure and integration tests.

## Git

- Keep the repository owner's configured Git identity as author and committer.
- Every Codex-assisted commit must include: `Co-authored-by: Codex <codex@openai.com>`.
- The user authorizes staged, ordered commits on `main` during this implementation. Keep each commit coherent. Never include deployment secrets or IDE state.
- Use Windows-local `uv` for development and tests; do not use WSL. Linux container acceptance is a separate deployment check.
- The user will perform real NAS/GTNH acceptance. Do not expand local work into strict GTNH/AstrBot/Docker environment testing; keep deployment and recovery documentation complete.
