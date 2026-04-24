# Legacy docs (`server.py` era)

These five documents describe `server.py` / `server.legacy.py` — the
monolithic MCP aggregator that Mimir replaces. They are kept for the
author's reference until Fase 8 cuts the production clients (Hermes,
Claude Desktop) over to `router.py`, and `server.legacy.py` itself is
deleted.

| File | What it documents |
|---|---|
| `INSTALL.md` | The author's specific install path (Windows + WSL, `HOMELAB_DIR=C:/homelab`, secrets layout). |
| `ARCHITECTURE.md` | The legacy aggregator architecture: hardcoded downstream mounts, `_build_subprocess_env` pattern, native_tools wiring. |
| `SECURITY.md` | The legacy threat model and secret resolution. Largely subsumed by `docs/security-model.md` for Mimir. |
| `BUGS.md` | Bug log of the legacy server. |
| `CHANGELOG.md` | Changelog up to v0.3.2 of the legacy package. |

For Mimir's own install, architecture, security and changelog, see
`docs/INSTALL.md`, `docs/ARCHITECTURE.md`, `docs/security-model.md` and
the git log on `refactor/generify-naming` respectively.

After Fase 8 these docs and `server.legacy.py` itself can be deleted.
