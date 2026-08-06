# Asyncio Event Loop Lifecycle Design

## Goal

Eliminate cross-event-loop reuse of asynchronous HTTP resources in both the interactive shell and one-shot CLI commands. A process has one top-level `asyncio.run()` for each invocation, and every runtime manager is created, used, and closed on that loop.

## Architecture

`SessionState` contains only serializable identity and authentication metadata. `RuntimeSession` combines that state with an `AsyncApiManager` and exists only inside its owner loop. `SessionController` owns at most one `RuntimeSession`, creates it during login, refreshes it in-place, and closes it before the loop exits.

Network commands expose awaitable `execute_*` functions. Typer wrappers are synchronous boundaries that run one coroutine for one-shot use. The interactive shell parses supported command arguments without invoking Typer command callbacks, then awaits the corresponding `execute_*` function against its existing `RuntimeSession`.

`AsyncApiManager` claims the first running loop that uses it. Initialize, requests, transfers, refresh, and close validate that owner. Cross-loop close fails before touching the old client.

## Lifecycle

- One-shot command: Typer wrapper → one `asyncio.run()` → create runtime → execute → close runtime.
- Interactive command: Typer wrapper → one `asyncio.run()` → login/create runtime → prompt loop → directly await commands → logout/exit → close runtime.
- `login --no-store` on a TTY enters the same ephemeral shell coroutine after login. Non-TTY use exits with code 2 and points to `pansh --ephemeral <command>`.

## Diagnostics and packaging

`pansh doctor` reports non-secret version, build, interpreter, dependency, profile, mode, package-path, and credential-store information. A small PEP 517 backend wrapper delegates to Hatchling while writing the build commit into wheel content from `PANSH_BUILD_COMMIT` or the checked-out Git commit, restoring the source file after the build.

The project is already version `3.1.3`; the fix preserves that requested target rather than creating an unrelated `3.1.4` release.

## Testing

All lifecycle tests are offline and use fake managers/clients. They cover one-loop shell sequences and repetitions, direct async dispatch, one-shot lifecycle, persistent and ephemeral cleanup, no-store TTY behavior, manager ownership failures, state purity, doctor redaction, and wheel commit metadata. Existing profile, migration, authentication, transfer, compile, and lint checks remain required.
