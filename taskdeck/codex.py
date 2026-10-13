"""Documented app-server JSON-RPC. No model/API credentials handled here."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Callable

from websockets.asyncio.client import unix_connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from .core import DeckError


class Codex:
    def __init__(self, argv, cwd, event: Callable, stderr):
        self.argv, self.cwd, self.event, self.stderr = argv, cwd, event, stderr
        self.proc = None
        self.pending = {}
        self.serial = 0
        self.reader = None
        self.endpoint = None
        self.server = None
        self.websocket = None

    async def connect(self):
        if self.proc and self.proc.returncode is None:
            return
        if not isinstance(self.argv, list) or not self.argv or not all(isinstance(x, str) for x in self.argv):
            raise DeckError("codex_command 必须是命令参数列表")
        env = dict(os.environ, NO_COLOR="1")
        argv = self.argv
        try:
            if self.endpoint:
                socket = Path(self.endpoint.removeprefix("unix://"))
                socket.unlink(missing_ok=True)
                self.server = await asyncio.create_subprocess_exec(
                    *argv, "--listen", self.endpoint, cwd=self.cwd, env=env,
                    stdin=asyncio.subprocess.DEVNULL, stdout=self.stderr, stderr=self.stderr)
                for _ in range(100):
                    if socket.exists():
                        socket.chmod(0o600)
                        break
                    if self.server.returncode is not None:
                        raise DeckError("Codex app-server 启动失败，请查看 codex.log")
                    await asyncio.sleep(0.1)
                else:
                    await self.close()
                    raise DeckError("Codex app-server socket 启动超时")
                self.proc = self.server
                self.websocket = await unix_connect(str(socket), max_size=16 * 1024 * 1024, ping_interval=None)
            else:
                self.proc = await asyncio.create_subprocess_exec(
                    *argv, cwd=self.cwd, env=env, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=self.stderr, limit=16 * 1024 * 1024)
        except (OSError, InvalidHandshake, TimeoutError) as e:
            await self.close()
            raise DeckError(f"无法启动 Codex：{e}") from e
        self.reader = asyncio.create_task(self.read())
        await self.rpc("initialize", {"clientInfo": {"name": "taskdeck", "title": "Taskdeck", "version": "0.1.0"},
                                     "capabilities": {"experimentalApi": True}})
        await self.write({"method": "initialized", "params": {}})

    async def write(self, message):
        if not self.proc or self.proc.returncode is not None:
            raise DeckError("Codex 服务未连接")
        try:
            data = json.dumps(message, ensure_ascii=False)
            if self.websocket:
                await self.websocket.send(data)
            else:
                self.proc.stdin.write((data + "\n").encode())
                await self.proc.stdin.drain()
        except (ConnectionError, BrokenPipeError, ConnectionClosed) as e:
            raise DeckError("Codex 连接已断开") from e

    async def rpc(self, method, params=None, timeout=45):
        self.serial += 1
        ident = self.serial
        future = asyncio.get_running_loop().create_future()
        self.pending[ident] = future
        try:
            await self.write({"id": ident, "method": method, "params": params or {}})
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            raise DeckError(f"Codex {method} 超时；请核对会话状态后重试")
        finally:
            self.pending.pop(ident, None)

    async def read(self):
        reason = "Codex app-server 已退出"
        try:
            async for line in self.messages():
                message = json.loads(line)
                if "method" not in message:
                    future = self.pending.get(message.get("id"))
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(DeckError(message["error"].get("message", str(message["error"]))))
                        else:
                            future.set_result(message.get("result", {}))
                else:
                    await self.event(message)
        except Exception as e:
            reason = f"Codex 协议连接失败：{e}"
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(DeckError(reason))
            await self.event({"method": "taskdeck/disconnected", "params": {"reason": reason}})

    async def messages(self):
        if self.websocket:
            async for data in self.websocket:
                yield data
        else:
            while line := await self.proc.stdout.readline():
                yield line

    async def close(self):
        if self.websocket:
            await self.websocket.close()
        for proc in (self.proc, self.server):
            if proc and proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
        if self.endpoint:
            Path(self.endpoint.removeprefix("unix://")).unlink(missing_ok=True)


def question_result(params, answers):
    questions = params.get("questions", [])
    if not questions or any(q["id"] not in answers for q in questions):
        raise DeckError("请回答所有问题")
    return {"answers": {q["id"]: {"answers": [str(answers[q["id"]])]} for q in questions}}
