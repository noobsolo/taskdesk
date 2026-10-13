from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

from .codex import Codex, question_result
from .core import ACTIVE, DeckError, Project, lock, now, read_json, write_json
from .github import GitHub


class Service:
    def __init__(self, project, codex_factory=Codex):
        self.project = project
        self.codex_factory = codex_factory
        self.codex = None
        self.threads = {}
        self.loaded = set()
        self.requests = {}
        self.transcripts = {}
        self.dirty = set()
        self.locks = {}
        self.connection_lock = asyncio.Lock()
        self.task_cache = {}
        self.cache_time = 0
        self.shutdown = asyncio.Event()

    def task_for_thread(self, thread_id):
        ident = self.threads.get(thread_id)
        if not ident:
            return None
        if time.monotonic() - self.cache_time > 2 or ident not in self.task_cache:
            self.task_cache = self.project.scan()
            self.cache_time = time.monotonic()
        return self.task_cache.get(ident)

    def transcript(self, task):
        if task.id not in self.transcripts:
            self.transcripts[task.id] = read_json(task.local / "transcript.json", [])
        return self.transcripts[task.id]

    def item(self, task, item_id, kind="agentMessage", text=None, delta=None):
        transcript = self.transcript(task)
        entry = next((x for x in reversed(transcript) if x["id"] == item_id), None)
        if entry is None:
            entry = {"id": item_id, "kind": kind, "text": "", "time": now()}
            transcript.append(entry)
        if text is not None:
            entry["text"] = text
        if delta is not None:
            entry["text"] += delta
        self.dirty.add(task.id)

    def record_item(self, task, item):
        kind = item.get("type", "item")
        text = item.get("text", "")
        if kind == "userMessage":
            text = "\n".join(c.get("text", "") for c in item.get("content", []) if c.get("type") == "text")
        elif kind == "commandExecution":
            text = "$ " + item.get("command", "") + "\n" + (item.get("aggregatedOutput") or "")
        elif kind == "fileChange":
            text = "\n".join(x.get("path", "") for x in item.get("changes", []))
        elif kind in {"reasoning", "contextCompaction"}:
            return
        elif not text:
            text = json.dumps(item, ensure_ascii=False)
        self.item(task, item.get("id", str(time.time_ns())), kind, text=text)

    async def flush(self):
        for ident in list(self.dirty):
            try:
                task = self.project.task(ident)
                write_json(task.local / "transcript.json", self.transcripts[ident])
                self.dirty.discard(ident)
            except DeckError:
                pass

    async def event(self, message):
        method, params = message["method"], message.get("params", {})
        if method == "taskdeck/disconnected":
            self.loaded.clear()
            self.requests.clear()
            for task in self.project.scan().values():
                if task.state.get("status") in ACTIVE:
                    task.save(status="disconnected", turn_id=None, error=params["reason"])
                    task.event("disconnected", reason=params["reason"])
            return
        task = self.task_for_thread(params.get("threadId"))
        if not task:
            if "id" in message:
                await self.codex.write({"id": message["id"], "error": {"code": -32601, "message": "Unassigned server request"}})
            return
        if "id" in message:
            request = {"id": message["id"], "method": method, "params": params}
            self.requests[(task.id, str(message["id"]))] = request
            task.save(status="waiting")
            task.event("request", method=method, params=params)
        elif method == "turn/started":
            turn = params["turn"]
            task.save(status="running", turn_id=turn["id"], last_turn_id=turn["id"], error=None)
            task.event("turn_started", turn_id=turn["id"])
        elif method == "turn/completed":
            turn = params["turn"]
            status = {"completed": "idle", "interrupted": "interrupted", "failed": "failed"}.get(turn["status"], "idle")
            # Late events from an earlier turn must not overwrite the active one.
            if task.state.get("turn_id") not in {None, turn["id"]}:
                return
            task.save(status=status, turn_id=None, last_turn_id=turn["id"], error=turn.get("error"))
            for key in list(self.requests):
                if key[0] == task.id:
                    self.requests.pop(key)
            task.event("turn_completed", turn_id=turn["id"], status=turn["status"])
            await self.flush()
        elif method == "item/agentMessage/delta":
            self.item(task, params["itemId"], delta=params["delta"])
        elif method in {"item/started", "item/completed"}:
            self.record_item(task, params["item"])
        elif method == "item/commandExecution/outputDelta":
            self.item(task, params["itemId"], "commandExecution", delta=params["delta"])
        elif method == "turn/plan/updated":
            task.save(plan=params.get("plan", []))
        elif method == "error":
            task.save(error=params.get("error"))
        elif method == "serverRequest/resolved":
            self.requests.pop((task.id, str(params["requestId"])), None)
            if not any(k[0] == task.id for k in self.requests) and task.state.get("turn_id"):
                task.save(status="running")
        elif method == "thread/status/changed":
            status = params.get("status", {})
            if status.get("type") == "active" and status.get("activeFlags"):
                task.save(status="waiting")

    async def connect(self):
        async with self.connection_lock:
            if not self.codex or (getattr(self.codex, "proc", None) and self.codex.proc.returncode is not None):
                self.loaded.clear()
                stderr = (self.project.runtime / "codex.log").open("ab")
                self.codex = self.codex_factory(self.project.config.get("codex_command", ["codex", "app-server"]),
                                               self.project.root, self.event, stderr)
            await self.codex.connect()

    async def load_thread(self, task, path):
        await self.connect()
        state = task.state
        params = {"cwd": str(path)}
        # Omit model and approval overrides unless explicitly configured by the user.
        if self.project.config.get("model"):
            params["model"] = self.project.config["model"]
        if state.get("thread_id"):
            thread_id = state["thread_id"]
            self.threads[thread_id] = task.id
            if thread_id not in self.loaded:
                result = await self.codex.rpc("thread/resume", {"threadId": thread_id, "excludeTurns": True, **params})
                self.loaded.add(thread_id)
                # Do not start a turn on resume. Fetch real saved messages page by page.
                await self.load_history(task, thread_id)
                status = result.get("thread", {}).get("status", {})
                if status.get("type") == "active":
                    task.save(status="running")
            return thread_id
        result = await self.codex.rpc("thread/start", params)
        thread_id = result["thread"]["id"]
        self.threads[thread_id] = task.id
        self.loaded.add(thread_id)
        task.save(thread_id=thread_id, status="idle")
        try:
            await self.codex.rpc("thread/name/set", {"threadId": thread_id, "name": f"{self.project.root.name}/{task.id} {task.title}"})
        except DeckError as e:
            task.event("name_warning", error=str(e))
        return thread_id

    async def load_history(self, task, thread_id):
        try:
            turns, cursor = [], None
            while True:
                params = {"threadId": thread_id, "limit": 100, "sortDirection": "asc", "itemsView": "full"}
                if cursor:
                    params["cursor"] = cursor
                result = await self.codex.rpc("thread/turns/list", params)
                turns.extend(result.get("data", []))
                cursor = result.get("nextCursor")
                if not cursor:
                    break
            if turns:
                self.transcripts[task.id] = []
                for turn in turns:
                    for item in turn.get("items", []):
                        self.record_item(task, item)
                active = next((t for t in reversed(turns) if t.get("status") == "inProgress"), None)
                if active:
                    task.save(turn_id=active["id"], status="running")
        except DeckError:
            result = await self.codex.rpc("thread/read", {"threadId": thread_id, "includeTurns": True})
            for turn in result.get("thread", {}).get("turns", []):
                for item in turn.get("items", []):
                    self.record_item(task, item)

    async def start(self, task, text=None):
        if task.state.get("status") in ACTIVE:
            return task.state
        if task.state.get("completed_commit") and self.project.has_commit(task.state["completed_commit"]):
            raise DeckError("任务已交付到基线")
        if task.definition.get("source_status") == "已完成":
            raise DeckError("原文档标记已完成，请先点击「确认完成」核对交付提交；如需重做，先修改 task.yaml 的 source_status。")
        task.save(status="preparing", error=None)
        try:
            await asyncio.to_thread(self.project.sync_base)
            blockers = self.project.blockers(task)
            if blockers:
                raise DeckError("; ".join(blockers))
            path = await asyncio.to_thread(self.project.ensure_worktree, task)
            fresh = not task.state.get("thread_id")
            thread_id = await self.load_thread(task, path)
            prompt = text or (self.project.prompt(task, path) if fresh else "继续当前任务，检查已完成的修改与验证记录，完成剩余工作并形成 PR。")
            result = await self.codex.rpc("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": prompt}]})
            turn = result["turn"]
            if task.state.get("last_turn_id") != turn["id"]:
                task.save(status="running", turn_id=turn["id"], last_turn_id=turn["id"])
            task.event("manual_start", thread_id=thread_id)
            return task.state
        except Exception as e:
            if task.state.get("status") == "preparing":
                task.save(status="failed", error=str(e))
            task.event("start_failed", error=str(e))
            raise

    async def send(self, task, text):
        if not text.strip():
            raise DeckError("消息不能为空")
        state = task.state
        if not state.get("thread_id"):
            raise DeckError("请先手动开始任务")
        path = await asyncio.to_thread(self.project.ensure_worktree, task)
        thread_id = await self.load_thread(task, path)
        turn_id = task.state.get("turn_id")
        if turn_id:
            await self.codex.rpc("turn/steer", {"threadId": thread_id, "expectedTurnId": turn_id,
                                                "input": [{"type": "text", "text": text}]})
            task.event("steer", text=text, turn_id=turn_id)
        else:
            result = await self.codex.rpc("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": text}]})
            turn = result["turn"]
            if task.state.get("last_turn_id") != turn["id"]:
                task.save(status="running", turn_id=turn["id"], last_turn_id=turn["id"])
            task.event("message", text=text)
        return task.state

    async def answer(self, task, request_id, response):
        key = (task.id, str(request_id))
        request = self.requests.get(key)
        if not request:
            raise DeckError("该请求已经结束，请刷新会话")
        method, params = request["method"], request["params"]
        if method == "item/tool/requestUserInput":
            result = question_result(params, response)
        elif method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            if response not in {"accept", "decline", "cancel"}:
                raise DeckError("审批结果必须是 accept/decline/cancel")
            result = {"decision": response}
        elif method == "item/permissions/requestApproval":
            result = {"permissions": params["permissions"] if response == "accept" else {}, "scope": "turn"}
        elif method == "mcpServer/elicitation/request":
            result = response if isinstance(response, dict) else {"action": response, "content": None}
        else:
            raise DeckError(f"不支持的服务器请求：{method}")
        await self.codex.write({"id": request["id"], "result": result})
        self.requests.pop(key, None)
        task.event("answer", request_id=request_id)
        return {"answered": True}

    async def dispatch(self, request):
        op = request.get("op")
        if op == "snapshot":
            result = await asyncio.to_thread(self.project.snapshot)
            result["service"] = True
            return result
        if op == "shutdown":
            self.shutdown.set()
            return {"stopping": True}
        if op == "sync":
            await asyncio.to_thread(self.project.sync_base)
            errors = []
            try:
                github = GitHub(self.project)
                for task in self.project.scan().values():
                    if task.state.get("branch"):
                        try:
                            await asyncio.to_thread(github.sync, task)
                        except DeckError as e:
                            errors.append(f"{task.id}: {e}")
            except DeckError as e:
                errors.append(str(e))
            return {"synced": True, "warnings": errors}
        task = self.project.task(request.get("task"))
        if op == "view":
            return {"transcript": self.transcript(task), "state": task.state,
                    "requests": [v for k, v in self.requests.items() if k[0] == task.id]}
        if op == "diff":
            return {"diff": await asyncio.to_thread(self.project.diff, task.id)}
        async with self.locks.setdefault(task.id, asyncio.Lock()):
            if op == "start":
                return await self.start(task, request.get("text"))
            if op == "send":
                return await self.send(task, request.get("text", ""))
            if op == "stop":
                state = task.state
                if not state.get("turn_id") or not self.codex:
                    raise DeckError("没有可中断的运行轮次")
                await self.codex.rpc("turn/interrupt", {"threadId": state["thread_id"], "turnId": state["turn_id"]})
                task.event("interrupt_requested", turn_id=state["turn_id"])
                return {"requested": True}
            if op == "answer":
                return await self.answer(task, request["request_id"], request["response"])
            if op == "pr":
                return await asyncio.to_thread(GitHub(self.project).create, task, request.get("title"), request.get("body"))
            if op == "complete":
                await asyncio.to_thread(self.project.record_completion, task.id, request["commit"])
                return task.state
        raise DeckError(f"未知操作：{op}")

    async def handle(self, reader, writer):
        try:
            data = await asyncio.wait_for(reader.readline(), 10)
            request = json.loads(data)
            result = await self.dispatch(request)
            response = {"ok": True, "result": result}
        except Exception as e:
            response = {"ok": False, "error": str(e)}
        try:
            writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode())
            await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    async def run(self):
        self.project.runtime.mkdir(parents=True, exist_ok=True)
        # Restart only marks interrupted ownership; it never starts/resumes Codex work.
        for task in self.project.scan().values():
            if task.state.get("thread_id"):
                self.threads[task.state["thread_id"]] = task.id
            if task.state.get("status") in ACTIVE:
                task.save(status="disconnected", turn_id=None, error="服务重启，等待手动恢复")
        parent = self.project.socket.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if parent.stat().st_uid != os.getuid() or parent.is_symlink():
            raise DeckError("服务 socket 目录归属不正确")
        parent.chmod(0o700)
        self.project.socket.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self.handle, path=self.project.socket, limit=16 * 1024 * 1024)
        self.project.socket.chmod(0o600)
        write_json(self.project.runtime / "service.json", {"pid": os.getpid(), "socket": str(self.project.socket), "started_at": now()})
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.shutdown.set)
        try:
            async with server:
                while not self.shutdown.is_set():
                    try:
                        await asyncio.wait_for(self.shutdown.wait(), 0.25)
                    except asyncio.TimeoutError:
                        await self.flush()
        finally:
            server.close()
            await server.wait_closed()
            await self.flush()
            if self.codex:
                await self.codex.close()
            self.project.socket.unlink(missing_ok=True)


def rpc(project, request, timeout=150):
    client = socket.socket(socket.AF_UNIX)
    client.settimeout(timeout)
    try:
        client.connect(str(project.socket))
        client.sendall((json.dumps(request, ensure_ascii=False) + "\n").encode())
        with client.makefile("r") as f:
            line = f.readline()
        if not line:
            raise DeckError("任务服务连接已断开")
        result = json.loads(line)
        if not result["ok"]:
            raise DeckError(result["error"])
        return result["result"]
    except (OSError, ValueError) as e:
        raise DeckError(f"任务服务未连接：{e}") from e
    finally:
        client.close()


def online(project):
    try:
        rpc(project, {"op": "snapshot"}, timeout=3)
        return True
    except DeckError:
        return False


def ensure_service(project):
    if online(project):
        return
    project.runtime.mkdir(parents=True, exist_ok=True)
    with (project.runtime / "service.log").open("ab") as log:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
        subprocess.Popen([sys.executable, "-m", "taskdeck.service", str(project.root)],
                         cwd=project.root, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         env=env, start_new_session=True, close_fds=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if project.socket.exists() and online(project):
            return
        time.sleep(0.1)
    raise DeckError(f"任务服务启动失败，查看 {project.runtime / 'service.log'}")


def snapshot(project):
    if project.socket.exists():
        try:
            return rpc(project, {"op": "snapshot"}, timeout=5)
        except DeckError:
            pass
    result = project.snapshot()
    result["service"] = False
    for row in result["tasks"]:
        if row["status"] in ACTIVE:
            row.update(status="disconnected", label="需恢复")
    return result


def view(project, ident):
    if project.socket.exists():
        try:
            return rpc(project, {"op": "view", "task": ident}, timeout=5)
        except DeckError:
            pass
    task = project.task(ident)
    return {"transcript": read_json(task.local / "transcript.json", []), "state": task.state, "requests": []}


if __name__ == "__main__":
    project = Project(sys.argv[1])
    with lock(project.runtime / "service.lock", blocking=False):
        asyncio.run(Service(project).run())
