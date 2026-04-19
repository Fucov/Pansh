"""Typer application entry point for PanCLI."""

from __future__ import annotations

import asyncio
import getpass
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
from rich.table import Table
from rich.tree import Tree

from .api import AsyncApiManager, InvalidRootException, WrongPasswordException
from .auth import rsa_encrypt
from .config import AUTH_FILE, load_config, save_config
from .models import MatchField, SelectedRemoteItem, TransferStatus, TransferTask
from .progress import format_bytes
from .selectors import filter_remote_items, select_local_files
from .settings import get_settings_path, load_settings, reload_settings
from .theme import UIOptions, create_console
from .transfer import batch_download, batch_upload
from .version import __version__

logger = logging.getLogger(__name__)
app = typer.Typer(name="pancli", no_args_is_help=False, invoke_without_command=True)
trash_app = typer.Typer(help="Trash management")
app.add_typer(trash_app, name="trash")


@dataclass
class AppState:
    ui: UIOptions
    console: Any
    stderr_console: Any
    settings: Any
    debug: bool = False


def _run(coro):
    return asyncio.run(coro)


def _configure_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _state(ctx: typer.Context) -> AppState:
    return ctx.obj  # type: ignore[return-value]


def _json_print(data: Any) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


def _error(message: str, *, code: int = 1) -> None:
    typer.echo(message, err=True)
    raise typer.Exit(code=code)


def _fmt_ts(value: int) -> str:
    if value <= 0:
        return "-"
    if value > 10**15:
        value = value // 1_000_000
    elif value > 10**12:
        value = value // 1_000
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))


def _normalize_remote_path(path: str, home_root: str) -> str:
    cwd = os.environ.get("PANCLI_REMOTE_CWD", home_root)
    if path in ("", "."):
        raw = cwd
    elif path.startswith("/"):
        raw = path
    else:
        raw = f"{cwd.rstrip('/')}/{path}" if cwd != "/" else f"/{path}"
    parts: list[str] = []
    for chunk in raw.split("/"):
        if not chunk or chunk == ".":
            continue
        if chunk == "..":
            if parts:
                parts.pop()
            continue
        parts.append(chunk)
    return "/" + "/".join(parts)


async def _login(console: Any) -> tuple[AsyncApiManager, str]:
    cfg = load_config()
    username = cfg.username or console.input("Username: ")
    encrypted = cfg.encrypted
    password: str | None = None
    if not encrypted or not cfg.store_password:
        password = getpass.getpass("Password: ")
        encrypted = rsa_encrypt(password, cfg.pubkey)
        if cfg.store_password:
            cfg.encrypted = encrypted
    for attempt in range(3):
        manager = AsyncApiManager(
            cfg.host,
            username,
            password,
            cfg.pubkey,
            encrypted=encrypted,
            cached_token=cfg.cached_token.token or None,
            cached_expire=cfg.cached_token.expires or None,
        )
        try:
            with console.status("Connecting..."):
                started = time.perf_counter()
                await manager.initialize()
                logger.debug("login took %.3fs", time.perf_counter() - started)
            cfg.username = username
            cfg.cached_token.token = manager._tokenid
            cfg.cached_token.expires = manager._expires
            save_config(cfg)
            entrydoc = await manager.get_entrydoc()
            if not entrydoc:
                await manager.close()
                _error("Unable to read entry document library.")
            return manager, "/" + entrydoc[0]["name"]
        except WrongPasswordException:
            await manager.close()
            if attempt == 2:
                break
            console.print("Wrong password, try again.", style="warning")
            password = getpass.getpass("Password: ")
            encrypted = rsa_encrypt(password, cfg.pubkey)
            cfg.encrypted = encrypted
    _error("Authentication failed.")
    raise RuntimeError("unreachable")


async def _with_manager(ctx: typer.Context) -> tuple[AsyncApiManager, str]:
    return await _login(_state(ctx).console)


async def _collect_remote_items(
    manager: AsyncApiManager,
    root_path: str,
    *,
    recursive: bool,
) -> list[SelectedRemoteItem]:
    info = await manager.get_resource_info_by_path(root_path.strip("/"))
    if info is None:
        return []
    if not info.is_dir:
        return [
            SelectedRemoteItem(
                remote_path=root_path,
                relative_path=info.name,
                basename=info.name,
                size=info.size,
                docid=info.docid,
            )
        ]
    items: list[SelectedRemoteItem] = []
    root_name = Path(root_path.rstrip("/")).name

    async def walk(docid: str, current_path: str, relative_prefix: str) -> None:
        dirs, files = await manager.list_dir(docid, by="name")
        for file in files:
            relative = f"{relative_prefix}/{file.name}".strip("/")
            items.append(
                SelectedRemoteItem(
                    remote_path=f"{current_path}/{file.name}".replace("//", "/"),
                    relative_path=relative,
                    basename=file.name,
                    size=file.size,
                    docid=file.docid,
                )
            )
        if not recursive:
            return
        for directory in dirs:
            await walk(
                directory.docid,
                f"{current_path}/{directory.name}".replace("//", "/"),
                f"{relative_prefix}/{directory.name}".strip("/"),
            )

    await walk(info.docid, root_path, root_name)
    return items


def _preview_local(console: Any, items: list[Any], *, title: str) -> None:
    table = Table(title=title)
    table.add_column("Name")
    table.add_column("Relative Path")
    table.add_column("Size", justify="right")
    for item in items[:100]:
        table.add_row(item.basename, item.relative_path, format_bytes(item.size))
    if len(items) > 100:
        table.add_row("...", "...", "...")
    console.print(table)
    console.print(f"{len(items)} item(s), {format_bytes(sum(item.size for item in items))}")


def _preview_remote(console: Any, items: list[SelectedRemoteItem], *, title: str) -> None:
    table = Table(title=title)
    table.add_column("Name")
    table.add_column("Remote Path")
    table.add_column("Size", justify="right")
    for item in items[:100]:
        table.add_row(item.basename, item.remote_path, format_bytes(item.size))
    if len(items) > 100:
        table.add_row("...", "...", "...")
    console.print(table)
    console.print(f"{len(items)} item(s), {format_bytes(sum(item.size for item in items))}")


def _confirm(console: Any, yes: bool, prompt: str) -> None:
    if yes:
        return
    if not typer.confirm(prompt):
        console.print("Cancelled.", style="muted")
        raise typer.Exit(code=1)


def _parse_upload_targets(items: list[str], has_selectors: bool) -> tuple[list[str], str]:
    if not items:
        if has_selectors:
            return ["."], "."
        _error("upload requires at least one source")
    if has_selectors:
        if len(items) == 1:
            return ["."], items[0]
        return items[:-1], items[-1]
    if len(items) == 1:
        return items, "."
    return items[:-1], items[-1]


def _parse_download_targets(items: list[str], has_selectors: bool) -> tuple[list[str], str]:
    if not items:
        if has_selectors:
            return ["."], "."
        _error("download requires at least one source")
    if has_selectors:
        if len(items) == 1:
            return items, "."
        return items[:-1], items[-1]
    if len(items) == 1:
        return items, "."
    return items[:-1], items[-1]


@app.callback(invoke_without_command=True)
def cli_callback(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Show version and exit."),
    whoami: bool = typer.Option(False, "--whoami", help="Show current account info."),
    logout: bool = typer.Option(False, "--logout", help="Delete cached credentials and token."),
    theme: str = typer.Option("auto", "--theme", help="Theme: auto/dark/light/plain."),
    plain: bool = typer.Option(False, "--plain", help="Plain high-compatibility output."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable colors."),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logs."),
) -> None:
    _configure_logging(debug)
    settings = load_settings()
    ui = UIOptions(
        theme_mode=theme if theme != "auto" else settings.theme_mode,
        plain=plain,
        no_color=no_color,
    )
    ctx.obj = AppState(
        ui=ui,
        console=create_console(ui),
        stderr_console=create_console(ui, stderr=True),
        settings=settings,
        debug=debug,
    )
    state = _state(ctx)
    if version:
        state.console.print(__version__)
        raise typer.Exit()
    if logout:
        cfg = load_config()
        cfg.username = None
        cfg.encrypted = None
        cfg.cached_token.token = ""
        cfg.cached_token.expires = 0
        save_config(cfg)
        state.console.print(f"Removed cached auth from {AUTH_FILE}")
        raise typer.Exit()
    if whoami and ctx.invoked_subcommand is None:
        whoami_command(ctx, json_output=False)
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        from .shell import run_interactive_shell

        run_interactive_shell(ui)
        raise typer.Exit()


@app.command("whoami")
def whoami_command(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    async def runner() -> None:
        state = _state(ctx)
        manager, home = await _with_manager(ctx)
        try:
            cfg = load_config()
            payload = {
                "host": cfg.host,
                "username": cfg.username,
                "home": home,
                "auth_file": str(AUTH_FILE),
                "settings_file": str(get_settings_path()),
            }
            if json_output:
                _json_print(payload)
                return
            table = Table(title="Current User")
            table.add_column("Field")
            table.add_column("Value")
            for key, value in payload.items():
                table.add_row(key, str(value or ""))
            state.console.print(table)
        finally:
            await manager.close()

    _run(runner())


@app.command()
def config(
    ctx: typer.Context,
    action: str = typer.Argument(..., help="show/get/set/reload/path"),
    key: str | None = typer.Argument(None),
    value: str | None = typer.Argument(None),
) -> None:
    state = _state(ctx)
    settings = reload_settings() if action == "reload" else load_settings()
    if action == "show":
        _json_print(settings.raw)
        return
    if action == "path":
        state.console.print(str(settings.path))
        return
    if action == "get":
        if not key:
            _error("config get requires a key")
        state.console.print(str(settings.get(key)))
        return
    if action == "set":
        if not key or value is None:
            _error("config set requires key and value")
        settings.set(key, value)
        settings.save()
        state.console.print(f"Updated {key}")
        return
    if action == "reload":
        state.console.print("Reloaded settings.")
        return
    _error(f"Unknown config action: {action}")


@app.command()
def ls(
    ctx: typer.Context,
    path: str = typer.Argument(".", help="Remote path."),
    human: bool = typer.Option(False, "--human", "-h", help="Human readable sizes."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    async def runner() -> None:
        state = _state(ctx)
        manager, home = await _with_manager(ctx)
        try:
            target = _normalize_remote_path(path, home)
            if target == "/":
                entrydoc = await manager.get_entrydoc()
                if json_output:
                    _json_print(entrydoc)
                    return
                for item in entrydoc:
                    state.console.print(item["name"], style="path")
                return
            info = await manager.get_resource_info_by_path(target.strip("/"))
            if info is None:
                _error(f"Path not found: {target}")
            if not info.is_dir:
                _error(f"Not a directory: {target}")
            dirs, files = await manager.list_dir(info.docid, by="name")
            payload = {
                "path": target,
                "dirs": [item.model_dump(mode="json") for item in dirs],
                "files": [item.model_dump(mode="json") for item in files],
            }
            if json_output:
                _json_print(payload)
                return
            if not dirs and not files:
                state.console.print("(empty)", style="muted")
                return
            table = Table(title=target)
            table.add_column("Type")
            table.add_column("Name")
            table.add_column("Size", justify="right")
            table.add_column("Modified")
            for item in dirs:
                table.add_row("dir", item.name, "-", _fmt_ts(item.modified))
            for item in files:
                table.add_row(
                    "file",
                    item.name,
                    format_bytes(item.size) if human else str(item.size),
                    _fmt_ts(item.modified),
                )
            state.console.print(table)
        finally:
            await manager.close()

    _run(runner())


@app.command()
def tree(
    ctx: typer.Context,
    path: str = typer.Argument(".", help="Remote path."),
    depth: int = typer.Option(3, "--depth", "-d"),
) -> None:
    async def runner() -> None:
        state = _state(ctx)
        manager, home = await _with_manager(ctx)
        try:
            target = _normalize_remote_path(path, home)
            info = await manager.get_resource_info_by_path(target.strip("/"))
            if info is None or not info.is_dir:
                _error(f"Not a directory: {target}")
            root = Tree(target)

            async def walk(docid: str, node: Tree, current: int) -> None:
                if current >= depth:
                    return
                dirs, files = await manager.list_dir(docid, by="name")
                for directory in dirs:
                    child = node.add(f"{directory.name}/")
                    await walk(directory.docid, child, current + 1)
                for file in files:
                    node.add(f"{file.name} ({format_bytes(file.size)})")

            await walk(info.docid, root, 0)
            state.console.print(root)
        finally:
            await manager.close()

    _run(runner())


@app.command()
def stat(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="Remote path."),
    json_output: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    async def runner() -> None:
        state = _state(ctx)
        manager, home = await _with_manager(ctx)
        try:
            target = _normalize_remote_path(path, home)
            info = await manager.get_resource_info_by_path(target.strip("/"))
            if info is None:
                _error(f"Path not found: {target}")
            meta = await manager.get_file_meta(info.docid)
            payload = {
                "path": target,
                "resource": info.model_dump(mode="json"),
                "metadata": meta.model_dump(mode="json"),
            }
            if json_output:
                _json_print(payload)
                return
            table = Table(title=target)
            table.add_column("Field")
            table.add_column("Value")
            table.add_row("docid", meta.docid)
            table.add_row("name", meta.name)
            table.add_row("size", format_bytes(meta.size))
            table.add_row("modified", _fmt_ts(meta.modified))
            table.add_row("client_mtime", _fmt_ts(meta.client_mtime))
            table.add_row("editor", meta.editor or "-")
            table.add_row("rev", meta.rev or "-")
            table.add_row("tags", ", ".join(meta.tags) if meta.tags else "-")
            state.console.print(table)
        finally:
            await manager.close()

    _run(runner())


@app.command()
def find(
    ctx: typer.Context,
    keyword: str = typer.Argument(...),
    path: str = typer.Option(".", "--path", "-p"),
    depth: int | None = typer.Option(None, "--depth", "-d"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    async def runner() -> None:
        state = _state(ctx)
        manager, home = await _with_manager(ctx)
        try:
            target = _normalize_remote_path(path, home)
            results = await manager.search(target, keyword, max_depth=depth or state.settings.search_depth)
            payload = [item.model_dump(mode="json") for item in results]
            if json_output:
                _json_print(payload)
                return
            if not results:
                state.console.print("No matches.", style="warning")
                raise typer.Exit(code=1)
            table = Table(title=f"find {keyword}")
            table.add_column("Type")
            table.add_column("Path")
            table.add_column("Size", justify="right")
            table.add_column("Modified")
            for item in results:
                table.add_row(
                    "dir" if item.is_dir else "file",
                    item.path,
                    "-" if item.is_dir else format_bytes(item.size),
                    _fmt_ts(item.modified),
                )
            state.console.print(table)
        finally:
            await manager.close()

    _run(runner())


@app.command(name="search")
def search_command(
    ctx: typer.Context,
    keyword: str = typer.Argument(...),
    path: str = typer.Option(".", "--path", "-p"),
    depth: int | None = typer.Option(None, "--depth", "-d"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    find(ctx, keyword, path, depth, json_output)


@app.command()
def quota(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    async def runner() -> None:
        state = _state(ctx)
        manager, _ = await _with_manager(ctx)
        try:
            quota_info = await manager.get_quota()
            payload = quota_info.model_dump(mode="json")
            if json_output:
                _json_print(payload)
                return
            table = Table(title="Quota")
            table.add_column("Field")
            table.add_column("Value")
            table.add_row("used", format_bytes(quota_info.quota_used))
            table.add_row("allocated", format_bytes(quota_info.quota_allocated))
            table.add_row("rate", quota_info.space_rate or "-")
            state.console.print(table)
        finally:
            await manager.close()

    _run(runner())


@app.command()
def mkdir(ctx: typer.Context, path: str = typer.Argument(...)) -> None:
    async def runner() -> None:
        manager, home = await _with_manager(ctx)
        try:
            await manager.create_dirs_by_path(_normalize_remote_path(path, home).strip("/"))
        except InvalidRootException as exc:
            _error(str(exc))
        finally:
            await manager.close()

    _run(runner())


@app.command()
def touch(ctx: typer.Context, path: str = typer.Argument(...)) -> None:
    async def runner() -> None:
        manager, home = await _with_manager(ctx)
        try:
            target = _normalize_remote_path(path, home)
            parent = "/".join(target.strip("/").split("/")[:-1])
            name = target.strip("/").split("/")[-1]
            parent_id = await manager.create_dirs_by_path(parent)
            await manager.upload_file(parent_id, name, b"", stream_len=0)
        finally:
            await manager.close()

    _run(runner())


@app.command()
def rm(
    ctx: typer.Context,
    path: str = typer.Argument(...),
    recursive: bool = typer.Option(False, "--recursive", "-r"),
) -> None:
    async def runner() -> None:
        manager, home = await _with_manager(ctx)
        try:
            target = _normalize_remote_path(path, home)
            info = await manager.get_resource_info_by_path(target.strip("/"))
            if info is None:
                _error(f"Path not found: {target}")
            if info.is_dir:
                if not recursive:
                    _error("Directory removal needs --recursive")
                await manager.delete_dir(info.docid)
            else:
                await manager.delete_file(info.docid)
        finally:
            await manager.close()

    _run(runner())


async def _move_or_copy(ctx: typer.Context, src: str, dst: str, *, force: bool, copy: bool) -> None:
    manager, home = await _with_manager(ctx)
    try:
        src_path = _normalize_remote_path(src, home)
        dst_path = _normalize_remote_path(dst, home)
        src_info = await manager.get_resource_info_by_path(src_path.strip("/"))
        if src_info is None:
            _error(f"Source not found: {src_path}")
        dst_info = await manager.get_resource_info_by_path(dst_path.strip("/"))
        op = manager.copy_file if copy else manager.move_file
        if dst_info and dst_info.is_dir:
            await op(src_info.docid, dst_info.docid, overwrite_on_dup=force)
            return
        dst_parent = "/".join(dst_path.strip("/").split("/")[:-1])
        dst_name = dst_path.strip("/").split("/")[-1]
        parent_info = await manager.get_resource_info_by_path(dst_parent)
        if parent_info is None:
            _error(f"Destination parent not found: {dst_parent}")
        if dst_info and not force:
            _error(f"Destination exists: {dst_path}")
        if dst_info and force:
            await manager.delete_file(dst_info.docid)
        new_id, new_name = await op(src_info.docid, parent_info.docid, rename_on_dup=True)
        if new_name != dst_name:
            await manager.rename_file(new_id, dst_name)
    finally:
        await manager.close()


@app.command()
def mv(
    ctx: typer.Context,
    src: str = typer.Argument(...),
    dst: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    _run(_move_or_copy(ctx, src, dst, force=force, copy=False))


@app.command()
def cp(
    ctx: typer.Context,
    src: str = typer.Argument(...),
    dst: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    _run(_move_or_copy(ctx, src, dst, force=force, copy=True))


@app.command()
def cat(
    ctx: typer.Context,
    path: str = typer.Argument(...),
    head: int = typer.Option(0, "--head"),
    tail: int = typer.Option(0, "--tail"),
) -> None:
    async def runner() -> None:
        manager, home = await _with_manager(ctx)
        try:
            target = _normalize_remote_path(path, home)
            info = await manager.get_resource_info_by_path(target.strip("/"))
            if info is None or info.is_dir:
                _error(f"Not a file: {target}")
            data = bytearray()
            async for chunk in manager.download_file_stream(info.docid):
                data.extend(chunk)
            lines = data.decode("utf-8", errors="replace").splitlines()
            if head > 0:
                lines = lines[:head]
            elif tail > 0:
                lines = lines[-tail:]
            typer.echo("\n".join(lines))
        finally:
            await manager.close()

    _run(runner())


@app.command()
def link(
    ctx: typer.Context,
    path: str = typer.Argument(...),
    create: bool = typer.Option(False, "--create", "-c"),
    delete: bool = typer.Option(False, "--delete", "-d"),
    expire: int = typer.Option(0, "--expire", "-e"),
    password: bool = typer.Option(False, "--password", "-p"),
) -> None:
    async def runner() -> None:
        state = _state(ctx)
        manager, home = await _with_manager(ctx)
        try:
            target = _normalize_remote_path(path, home)
            info = await manager.get_resource_info_by_path(target.strip("/"))
            if info is None:
                _error(f"Path not found: {target}")
            if create:
                result = await manager.create_link(info.docid, end_time=expire or None, enable_pass=password)
                state.console.print(result.link)
                if result.password:
                    state.console.print(f"password: {result.password}")
                return
            if delete:
                await manager.delete_link(info.docid)
                return
            result = await manager.get_link(info.docid)
            if result is None:
                _error("No link exists for this path.")
            state.console.print(result.link)
            if result.password:
                state.console.print(f"password: {result.password}")
        finally:
            await manager.close()

    _run(runner())


@app.command()
def upload(
    ctx: typer.Context,
    items: list[str] = typer.Argument(..., help="Sources followed by remote destination."),
    glob_patterns: list[str] = typer.Option([], "--glob", help="Glob pattern to filter local files."),
    regex: str | None = typer.Option(None, "--regex", help="Regex pattern to filter local files."),
    exclude: list[str] = typer.Option([], "--exclude", help="Glob exclude pattern."),
    recursive: bool = typer.Option(False, "--recursive", "-r"),
    jobs: int | None = typer.Option(None, "--jobs", "-j"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    match_field: MatchField = typer.Option(MatchField.BASENAME, "--match-field"),
) -> None:
    async def runner() -> None:
        state = _state(ctx)
        manager, home = await _with_manager(ctx)
        try:
            sources, remote = _parse_upload_targets(items, bool(glob_patterns or regex or exclude or recursive))
            if not (glob_patterns or regex or exclude):
                for source in sources:
                    if Path(source).expanduser().is_dir() and not recursive:
                        _error(f"Directory upload needs --recursive: {source}")
            remote_dir = _normalize_remote_path(remote, home)
            selected = select_local_files(
                sources,
                globs=glob_patterns,
                regex=regex,
                excludes=exclude,
                recursive=recursive or bool(glob_patterns or regex or exclude),
                match_field=match_field,
            )
            if not selected:
                _error("No local files matched.")
            _preview_local(state.console, selected, title="Upload Preview")
            _confirm(state.console, yes, "Continue upload?")
            tasks: list[TransferTask] = []
            for item in selected:
                remote_path = f"{remote_dir.rstrip('/')}/{item.relative_path.replace('\\', '/')}"
                parent = "/".join(remote_path.strip("/").split("/")[:-1])
                parent_id = await manager.create_dirs_by_path(parent)
                tasks.append(
                    TransferTask(
                        remote_path=remote_path,
                        local_path=item.source_path,
                        size=item.size,
                        docid=parent_id,
                    )
                )
            await batch_upload(manager, tasks, jobs=jobs or state.settings.default_jobs, console=state.console)
            failed = [task for task in tasks if task.status == TransferStatus.FAILED]
            if failed:
                for task in failed:
                    state.stderr_console.print(f"FAILED {task.local_path}: {task.error}")
                raise typer.Exit(code=1)
        finally:
            await manager.close()

    _run(runner())


@app.command()
def download(
    ctx: typer.Context,
    items: list[str] = typer.Argument(..., help="Remote source(s) followed by local destination."),
    glob_patterns: list[str] = typer.Option([], "--glob", help="Glob pattern to filter remote files."),
    regex: str | None = typer.Option(None, "--regex", help="Regex pattern to filter remote files."),
    exclude: list[str] = typer.Option([], "--exclude", help="Glob exclude pattern."),
    recursive: bool = typer.Option(False, "--recursive", "-r"),
    search: bool = typer.Option(False, "--search", help="Prefer search mode when available."),
    range_scan: bool = typer.Option(False, "--range", help="Treat remote arguments as search roots."),
    jobs: int | None = typer.Option(None, "--jobs", "-j"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    match_field: MatchField = typer.Option(MatchField.BASENAME, "--match-field"),
) -> None:
    async def runner() -> None:
        state = _state(ctx)
        manager, home = await _with_manager(ctx)
        try:
            selector_mode = bool(glob_patterns or regex or exclude or search or range_scan)
            roots, dest = _parse_download_targets(
                items,
                bool(glob_patterns or regex or exclude or recursive or search or range_scan),
            )
            dest_dir = Path(dest).expanduser().resolve()
            dest_dir.mkdir(parents=True, exist_ok=True)
            normalized_roots = [_normalize_remote_path(root, home) for root in roots]
            if not selector_mode and not recursive:
                for root in normalized_roots:
                    info = await manager.get_resource_info_by_path(root.strip("/"))
                    if info and info.is_dir:
                        _error(f"Directory download needs --recursive: {root}")
            remote_items: list[SelectedRemoteItem] = []
            for root in normalized_roots:
                remote_items.extend(
                    await _collect_remote_items(
                        manager,
                        root,
                        recursive=recursive or range_scan or bool(glob_patterns or regex),
                    )
                )
            if glob_patterns or regex or exclude:
                remote_items = filter_remote_items(
                    remote_items,
                    globs=glob_patterns,
                    regex=regex,
                    excludes=exclude,
                    match_field=match_field,
                )
            if search and not remote_items:
                state.stderr_console.print("Search mode fallback did not find any items.")
            if not remote_items:
                _error("No remote files matched.")
            _preview_remote(state.console, remote_items, title="Download Preview")
            _confirm(state.console, yes, "Continue download?")
            tasks = [
                TransferTask(
                    remote_path=item.remote_path,
                    local_path=str(dest_dir / item.relative_path),
                    size=item.size,
                    docid=item.docid,
                )
                for item in remote_items
            ]
            await batch_download(manager, tasks, jobs=jobs or state.settings.default_jobs, console=state.console)
            failed = [task for task in tasks if task.status == TransferStatus.FAILED]
            if failed:
                for task in failed:
                    state.stderr_console.print(f"FAILED {task.remote_path}: {task.error}")
                raise typer.Exit(code=1)
        finally:
            await manager.close()

    _run(runner())


@app.command()
def revisions(ctx: typer.Context, path: str = typer.Argument(...)) -> None:
    async def runner() -> None:
        state = _state(ctx)
        manager, home = await _with_manager(ctx)
        try:
            target = _normalize_remote_path(path, home)
            info = await manager.get_resource_info_by_path(target.strip("/"))
            if info is None or info.is_dir:
                _error(f"Not a file: {target}")
            revision_items = await manager.get_revisions(info.docid)
            table = Table(title=f"Revisions: {target}")
            table.add_column("rev")
            table.add_column("size", justify="right")
            table.add_column("modified")
            table.add_column("editor")
            for item in revision_items:
                table.add_row(item.rev, format_bytes(item.size), _fmt_ts(item.modified), item.editor or "-")
            state.console.print(table)
        finally:
            await manager.close()

    _run(runner())


@app.command("restore-revision")
def restore_revision(
    ctx: typer.Context,
    path: str = typer.Argument(...),
    rev: str = typer.Argument(...),
) -> None:
    async def runner() -> None:
        manager, home = await _with_manager(ctx)
        try:
            target = _normalize_remote_path(path, home)
            info = await manager.get_resource_info_by_path(target.strip("/"))
            if info is None or info.is_dir:
                _error(f"Not a file: {target}")
            await manager.restore_revision(info.docid, rev)
        finally:
            await manager.close()

    _run(runner())


@trash_app.command("ls")
def trash_ls() -> None:
    _error("Trash list is not implemented yet.", code=2)


@trash_app.command("restore")
def trash_restore() -> None:
    _error("Trash restore is not implemented yet.", code=2)


@trash_app.command("rm")
def trash_rm() -> None:
    _error("Trash remove is not implemented yet.", code=2)


@app.command()
def shell(ctx: typer.Context) -> None:
    from .shell import run_interactive_shell

    run_interactive_shell(_state(ctx).ui)


def main() -> None:
    app()


def cli() -> None:
    main()


if __name__ == "__main__":
    main()
