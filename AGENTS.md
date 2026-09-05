# Project guidance

## Current implementation

- Python 3.13 / uv. `src/gtnh_mcp` contains configuration, signed identity validation, RCON, MCP tools, archive validation, Docker control and the persistent restore worker.
- `astrbot_plugin` is the separately installed AstrBot bridge. Sign actual message identities; never accept identity or role from model arguments. Confirmation is a command, not an LLM tool.
- MCP and AstrBot use Linux Docker host networking. Only the restore helper mounts the Docker socket and server directories; MCP calls it through a Unix socket.
- Details belong in `docs/architecture.md`, `docs/security.md`, `docs/restore.md`, and `docs/deployment.md`.

## Development

- Install: `uv sync --locked --group dev`.
- Validate: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`.
- Build images: `docker compose build`. Real restore acceptance requires an isolated GTNH container and actual backup; never use production as the first test.
- Update this file when architecture, commands or constraints change. Update relevant docs alongside behavior/configuration changes. Record limitations honestly.

## Invariants

- No generic RCON tool, shell endpoint, arbitrary container selector, or caller-supplied filesystem path.
- Verify identity on every call; group/admin ACLs are server configuration. Never log tokens, passwords or signing secrets.
- Bind confirmation to actor, group, archive digest and expiry. Persist before side effects. Never automatically retry RCON writes.
- Hold the shared operation lock through restoration; confirm exit before moving directories. Preserve old directories and stop on uncertain recovery state.
- Destructive behavior requires meaningful failure and integration tests.

## Git

- Keep the repository owner's configured Git identity as author and committer.
- Every Codex-assisted commit must include: `Co-authored-by: Codex <codex@openai.com>`.
- The user authorizes staged, ordered commits on `main` during this implementation. Keep each commit coherent. Never include deployment secrets or IDE state.
- Use Windows-local `uv` for development and tests; do not use WSL. Linux container acceptance is a separate deployment check.
