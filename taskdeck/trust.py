"""Local opt-in for the native CLI's managed-worktree folder trust."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tomllib

from websockets.asyncio.client import unix_connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from .core import DeckError, git, read_json, write_json


def codex_home():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()


def _identity(project):
    return {"root": str(project.root), "project_id": project.config.get("project_id"),
            "codex_home": str(codex_home())}


def worktree_trust_enabled(project):
    saved = read_json(project.runtime / "trust-worktrees.json")
    if not isinstance(saved, dict):
        raise DeckError("本机 worktree 信任记录必须是对象")
    return (saved.get("version") == 1 and saved.get("enabled") is True
            and all(saved.get(key) == value for key, value in _identity(project).items()))


def _project_trust():
    config = codex_home() / "config.toml"
    try:
        data = tomllib.loads(config.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as e:
        raise DeckError("无法核对 Codex 的目录信任设置，请检查 CODEX_HOME/config.toml") from e
    return _trust_levels(data)


def _trust_levels(data):
    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        raise DeckError("Codex config.toml 的 projects 必须是对象")
    # Compare canonical paths as well as literal keys (macOS /tmp aliases,
    # symlinks and a trailing slash must not hide an explicit untrusted entry).
    levels = {}
    for path, settings in projects.items():
        level = settings.get("trust_level") if isinstance(settings, dict) else None
        if isinstance(level, str) and level in {"trusted", "untrusted"}:
            resolved = Path(path).expanduser().resolve()
            if levels.get(resolved) != "untrusted":
                levels[resolved] = level
    return levels


def set_worktree_trust(project, enabled):
    if enabled and _project_trust().get(project.root) == "untrusted":
        raise DeckError("项目根目录已被 Codex 明确标为 untrusted，请先在 Codex 中修改该选择")
    # This is deliberately outside the versioned task config. Cloning a repo
    # or pulling a changed .taskctl.yaml cannot authorize folder trust.
    write_json(project.runtime / "trust-worktrees.json",
               {"version": 1, "enabled": enabled, **_identity(project)})


def _eligible_worktree(project, task, cwd):
    if not worktree_trust_enabled(project):
        return None
    path = Path(cwd).resolve()
    expected = (task.local / "worktree").resolve()
    state = task.state
    if (path != expected or not path.is_relative_to(project.task_root)
            or Path(state.get("worktree") or project.root).resolve() != path):
        return None
    common = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir", check=False)
    top = git(path, "rev-parse", "--show-toplevel", check=False)
    if (common.returncode or top.returncode
            or Path(common.stdout.strip()).resolve() != project.common.resolve()
            or Path(top.stdout.strip()).resolve() != path):
        return None
    registered = git(project.root, "worktree", "list", "--porcelain", "-z").stdout.split("\0")
    if "worktree " + str(path) not in registered:
        return None
    levels = _project_trust()
    if levels.get(project.root) == "untrusted" or levels.get(path) in {"untrusted", "trusted"}:
        return None
    return path


async def inherit_worktree_trust(project, task, connection):
    """Persist one eligible folder's trust through Codex's versioned config API.

    Remote CLI onboarding reads the server's disk config, ignoring local -c
    trust overrides. A separate dashboard client avoids restarting its service.
    """
    path = await asyncio.to_thread(_eligible_worktree, project, task, connection["cwd"])
    if path is None:
        return
    if connection.get("endpoint") != "unix://" + str(project.codex_socket):
        raise DeckError("无法核对本项目的 Codex 接入端点，不会登记目录信任")
    try:
        async with unix_connect(str(project.codex_socket), max_size=16 * 1024 * 1024, ping_interval=None) as socket:
            serial = 0

            async def request(method, params):
                nonlocal serial
                serial += 1
                await socket.send(json.dumps({"id": serial, "method": method, "params": params}))
                async with asyncio.timeout(10):
                    while True:
                        response = json.loads(await socket.recv())
                        if response.get("id") == serial and "method" not in response:
                            if "error" in response:
                                raise DeckError("Codex 拒绝登记目录信任，配置可能已变化；请重新进入会话")
                            return response["result"]

            await request("initialize", {"clientInfo": {"name": "taskdeck-trust", "version": "0.1.0"}})
            await socket.send(json.dumps({"method": "initialized", "params": {}}))
            result = await request("config/read", {"cwd": str(path), "includeLayers": True})
            levels = _trust_levels(result["config"])
            if levels.get(project.root) == "untrusted" or levels.get(path) in {"trusted", "untrusted"}:
                return
            config_path = codex_home() / "config.toml"
            layer = next((layer for layer in result.get("layers") or []
                          if layer["name"].get("type") == "user"
                          and Path(layer["name"]["file"]).resolve() == config_path), None)
            if not layer or not isinstance(layer.get("version"), str):
                raise DeckError("Codex 服务使用了不同的配置目录或不支持版本核对，不会登记目录信任")
            # Recheck local authorization after network I/O, so a concurrent
            # disable or worktree move is respected before the config write.
            if await asyncio.to_thread(_eligible_worktree, project, task, connection["cwd"]) != path:
                return
            await request("config/value/write", {
                "filePath": str(config_path), "expectedVersion": layer["version"],
                "keyPath": f'projects.{json.dumps(str(path), ensure_ascii=False)}.trust_level',
                "value": "trusted", "mergeStrategy": "upsert"})
    except (OSError, TimeoutError, ConnectionClosed, InvalidHandshake) as e:
        raise DeckError("无法连接 Codex 核对目录信任，请重新进入会话") from e
