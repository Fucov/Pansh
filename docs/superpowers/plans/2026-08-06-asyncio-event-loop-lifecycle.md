# Asyncio Event Loop Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep every Pansh runtime session and HTTP client on one asyncio event loop while preserving one-shot CLI behavior and adding safe diagnostics.

**Architecture:** Split plain `SessionState` from loop-bound `RuntimeSession`, make network operations awaitable business functions, and dispatch shell commands directly on its runtime. Add manager loop ownership at the transport boundary and embed build metadata through Hatch.

**Tech Stack:** Python 3.10+, asyncio, Typer/Click, HTTPX/AnyIO, pytest, Hatchling, Ruff.

---

### Task 1: Session data/runtime boundary

**Files:**
- Modify: `src/pansh/session.py`
- Modify: `src/pansh/main.py`
- Test: `tests/test_session.py`
- Test: `tests/test_session_modes.py`

- [ ] Write tests asserting `SessionState` has only plain fields and `RuntimeSession` holds the manager separately.
- [ ] Run the focused tests and confirm imports/field assertions fail before implementation.
- [ ] Replace `Session` with `SessionState` and `RuntimeSession`; update controller create, refresh, close, logout, and `AppState` references.
- [ ] Run focused session tests and confirm runtime creation/use/close records one loop ID.

### Task 2: Manager loop ownership

**Files:**
- Modify: `src/pansh/api.py`
- Test: `tests/test_api_lifecycle.py`

- [ ] Write offline tests that initialize/request/transfer/close on one loop and reject reuse or close on another loop with `AsyncApiManager cannot be reused across event loops`.
- [ ] Run the tests and confirm cross-loop use is not yet rejected.
- [ ] Add `_owner_loop` and `_assert_owner_loop()`; call it at initialize, close, token refresh/check, request, upload, and download entry points before client access.
- [ ] Run focused API tests and confirm normal ownership succeeds while cross-loop access fails without closing the client.

### Task 3: Awaitable network commands

**Files:**
- Modify: `src/pansh/main.py`
- Test: `tests/test_commands.py`

- [ ] Write tests for awaitable whoami, ls, tree, stat, find, mkdir, touch, rm, mv, cp, cat, upload, and download handlers using a fake runtime manager.
- [ ] Run focused tests and confirm the execute functions do not exist.
- [ ] Extract command business logic into focused `execute_*` coroutines in the existing CLI module; keep rendering and errors behavior-compatible without introducing a circular command module.
- [ ] Make each Typer wrapper call one shared one-shot coroutine which acquires, executes, and closes its runtime on the same loop.
- [ ] Run focused command and existing CLI tests.

### Task 4: Direct interactive shell dispatch

**Files:**
- Modify: `src/pansh/shell.py`
- Test: `tests/test_shell_lifecycle.py`
- Test: `tests/test_shell.py`

- [ ] Write an offline scripted-shell test for login → ls → cd → ls → download → cd .. → ls → exit, plus 20 ls and two downloads, asserting a single loop ID and first/second command parity.
- [ ] Add tests patching `asyncio.run` and `typer.main.get_command` command invocation to prove no nested run and no shell network callback invocation.
- [ ] Run tests and confirm current threaded Typer dispatch fails them.
- [ ] Add shell-only argument parsing/dispatch that awaits `execute_*` handlers with the current `RuntimeSession`; retain Typer only for synchronous help rendering.
- [ ] Ensure prompt-loop finalization closes the runtime once for persistent and ephemeral sessions.
- [ ] Run shell lifecycle and CLI auth tests.

### Task 5: Top-level entry and no-store lifecycle

**Files:**
- Modify: `src/pansh/main.py`
- Modify: `src/pansh/shell.py`
- Test: `tests/test_cli_lifecycle.py`
- Test: `tests/test_cli_auth.py`

- [ ] Write tests counting `asyncio.run` calls for a one-shot command, normal shell, and TTY `login --no-store`; assert non-TTY exits before login.
- [ ] Run tests and confirm nested shell commands create additional loops.
- [ ] Centralize `run_one_shot()` and `run_interactive_shell_async()` so coroutine code only awaits and synchronous wrappers own the sole `asyncio.run()`.
- [ ] Map `--once` and `--no-store-login` to ephemeral mode and ensure no persistent store access in no-store login.
- [ ] Run focused lifecycle/auth tests.

### Task 6: Doctor and build metadata

**Files:**
- Create: `hatch_build.py`
- Create: `src/pansh/_build_commit.py`
- Modify: `pyproject.toml`
- Modify: `src/pansh/main.py`
- Test: `tests/test_doctor.py`
- Test: `tests/test_packaging.py`

- [ ] Write tests asserting doctor fields, secret redaction, and wheel commit metadata under `PANSH_BUILD_COMMIT`.
- [ ] Run tests and confirm doctor/build metadata are absent.
- [ ] Add safe doctor payload/output and a PEP 517 wrapper around Hatchling that embeds a validated commit SHA then restores the source placeholder.
- [ ] Build and inspect a wheel; run focused doctor/packaging tests.

### Task 7: Full verification and delivery

**Files:**
- Modify: `README.md` only if command documentation needs alignment.

- [ ] Run `uv run python -m pytest -q` and record total pass count.
- [ ] Run `uv run python -m compileall src`.
- [ ] Run `uv run python -m ruff check src tests hatch_build.py`.
- [ ] Build wheel and inspect embedded version/commit.
- [ ] Review the diff against all 16 lifecycle acceptance tests and secret-redaction requirements.
- [ ] Commit changes with `fix: keep async sessions on one event loop`, push the branch, and create a PR targeting `main`.
