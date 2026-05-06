# Provider Home Sync Threat Model

Provider home sync copies selected user-level provider configuration into CCB-managed isolated runtime homes. It is environment replication, not just credential propagation, so every copied entry must have an explicit reason and boundary.

## Scope

Supported providers:

- `codex`
- `claude`

Supported target homes are only CCB-managed runtime homes under:

```text
.ccb/agents/<agent>/provider-runtime/<provider>/<provider>-home
```

Homes backed by provider profiles or explicit provider home environment overrides are not synced. Those homes are user-managed by definition.

## Source To Target Matrix

| Provider | Source | Target | Entries | Credential behavior |
| --- | --- | --- | --- | --- |
| Codex | system `CODEX_HOME` | `.ccb/.../codex-home` | `config.toml`, `skills`, `commands`, `rules` | auth is copied only for explicit `sync-codex-home --include-auth` |
| Claude | `~/.claude` | `.ccb/.../claude-home/.claude` | `settings.json`, `CLAUDE.md`, `commands`, `agents`, `skills` | auth/session/runtime state is not copied |

Claude sync intentionally includes `CLAUDE.md` because it may contain global operator instructions required for isolated Claude agents. It intentionally excludes runtime state such as `projects`, `session-env`, logs, caches, credentials, and transient session data.

## Managed Home Guard

A target home is syncable only when all of these are true:

- The home exists and is a directory.
- The home is under the current project `.ccb` directory.
- No path component between `.ccb` and the target home is a symlink.
- The target has the provider sentinel, or it is a legacy CCB runtime home that can be safely marked in place.

Legacy marker migration is intentionally narrow. It only writes a sentinel into paths shaped like `.ccb/agents/<agent>/provider-runtime/<provider>/<provider>-home`. Missing homes are still skipped and are never created by sync.

## Symlink Policy

Top-level safe entries that are symlinks are skipped. Nested symlink files inside copied directories are dereferenced and copied as physical files. Nested symlink directories are skipped to avoid recursively copying external repositories or unrelated home content.

Broken symlinks are skipped with a warning. This keeps sync non-fatal while making missing skill targets visible during canary and doctor-driven investigations.

## Rejected Cases

Sync must refuse or skip:

- profile-backed provider homes
- target homes outside the project `.ccb`
- target homes reached through symlinked runtime ancestors
- top-level safe entries that are symlinks
- nested symlink directories
- missing runtime homes
- non-supported providers

## Operational Notes

`provider_home_sync` is opt-in in `.ccb/ccb.config`. A canary that does not enable the provider is only testing the no-op path.

After installing a new CCB tree, the already-running ccbd process may still be executing from the previous install directory. `ccb doctor` reports the daemon install path and whether it matches the current install path. Provider-home-sync canaries should only proceed when `ccbd_daemon_install_matches_current` is `True`, or after an intentional ccbd restart.

## Pre-Restart Canary Checklist

Before any restart:

- Confirm the new install path is active.
- Confirm the previous install path still exists for rollback.
- Confirm `ccb doctor` reports daemon install drift status.
- Confirm sync can run manually against a managed Claude home.

Restart-gated canary:

- Enable `provider_home_sync = ["claude"]`.
- Restart ccbd intentionally.
- Confirm doctor reports provider sync enabled and daemon install match.
- Launch or restore Claude agent.
- Verify `CLAUDE.md`, `settings.json`, `commands`, `agents`, and `skills` in isolated home.
- Verify symlinked `SKILL.md` files are physical files in the isolated home.
- Verify symlink directories are not copied.
- Run a round-trip `ccb ask` ping.
- Run one concurrent spawn canary before declaring the feature production-ready.
