# Project guidance

## Current implementation

- Python 3.13 / uv. `src/gtnh_mcp` contains configuration, signed identity validation, RCON, MCP tools, archive validation, Docker control and the persistent restore worker.
- `astrbot_plugin` is the separately installed AstrBot bridge. Sign actual message identities; never accept identity or role from model arguments. Confirmation is a command, not an LLM tool.
- Its signing secret comes from AstrBot's `GTNH_AUTH_SECRET`; plugin settings only contain the MCP URL. Do not register the service a second time through AstrBot native MCP configuration.
- MCP and AstrBot use Linux Docker host networking. Only the restore helper mounts the Docker socket and server directories; MCP calls it through a Unix socket.
- Details belong in `docs/architecture.md`, `docs/security.md`, `docs/restore.md`, `docs/configuration.md`, `docs/deployment.md`, and `docs/testing.md`.
- `compose.chat.yaml` is the optional Linux host-network AstrBot + NapCat deployment. Keep every `.env.example` variable documented in `docs/configuration.md` and the end-to-end NAS procedure in `docs/deployment.md`.

## Development

- Install: `uv sync --locked --group dev`.
- Validate: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`.
- Optional simulated black-box test: `uv run pytest -m blackbox -q`; excluded by default. Its test-only TCP helper transport does not replace production Unix-socket deployment verification.
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
- The user will perform real NAS/GTNH acceptance. Do not expand local work into strict GTNH/AstrBot/Docker environment testing; keep deployment and recovery documentation complete.
