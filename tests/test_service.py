import asyncio

import pytest

from taskdeck.core import DeckError, git
from taskdeck.service import Service


class FakeCodex:
    """Deterministic protocol fixture, never used by the production CLI."""
    def __init__(self, argv, cwd, event, stderr):
        self.event = event
        self.calls = []
        self.writes = []
        self.serial = 0

    async def connect(self):
        pass

    async def rpc(self, method, params=None, timeout=None):
        self.calls.append((method, params))
        if method == "thread/start":
            self.serial += 1
            return {"thread": {"id": f"thread-{self.serial}"}}
        if method == "turn/start":
            self.serial += 1
            turn = {"id": f"turn-{self.serial}", "status": "inProgress"}
            await self.event({"method": "turn/started", "params": {"threadId": params["threadId"], "turn": turn}})
            await self.event({"method": "item/started", "params": {"threadId": params["threadId"], "item": {
                "type": "userMessage", "id": turn["id"] + "-input", "content": params["input"]}}})
            return {"turn": turn}
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"], "status": {"type": "idle"}}}
        if method == "thread/turns/list":
            return {"data": [], "nextCursor": None}
        if method == "turn/interrupt":
            await self.event({"method": "turn/completed", "params": {"threadId": params["threadId"],
                                                                      "turn": {"id": params["turnId"], "status": "interrupted"}}})
        return {}

    async def write(self, message):
        self.writes.append(message)


def service(project):
    project.runtime.mkdir(parents=True, exist_ok=True)
    return Service(project, FakeCodex)


async def test_reading_and_creation_never_start_codex(project):
    project.add("T001", "one")
    svc = service(project)
    await svc.dispatch({"op": "snapshot"})
    await svc.dispatch({"op": "view", "task": "T001"})
    assert svc.codex is None
    assert not project.task("T001").local.exists()


async def test_manual_parallel_start_duplicate_start_steer_interrupt(project):
    a = project.add("T001", "one")
    b = project.add("T002", "two")
    svc = service(project)
    await asyncio.gather(svc.dispatch({"op": "start", "task": a.id}), svc.dispatch({"op": "start", "task": b.id}))
    assert a.state["thread_id"] != b.state["thread_id"]
    original = a.state["thread_id"]
    await svc.dispatch({"op": "start", "task": a.id})
    assert sum(m == "thread/start" for m, p in svc.codex.calls) == 2
    await svc.dispatch({"op": "send", "task": a.id, "text": "先运行测试"})
    method, params = svc.codex.calls[-1]
    assert method == "turn/steer" and params["threadId"] == original
    assert params["expectedTurnId"] == a.state["turn_id"]
    await svc.dispatch({"op": "stop", "task": a.id})
    assert a.state["status"] == "interrupted"
    assert b.state["status"] == "running"
    await svc.dispatch({"op": "send", "task": a.id, "text": "继续，缩小范围"})
    assert a.state["thread_id"] == original
    assert svc.codex.calls[-1][0] == "turn/start"


async def test_blocked_start_never_creates_thread(project):
    project.add("T001", "one")
    task = project.add("T002", "two", ["T001"])
    svc = service(project)
    with pytest.raises(DeckError, match="交付"):
        await svc.dispatch({"op": "start", "task": task.id})
    assert svc.codex is None
    assert not (task.local / "worktree").exists()


async def test_live_messages_questions_and_approval_are_scoped(project):
    task = project.add("T001", "one")
    svc = service(project)
    await svc.dispatch({"op": "start", "task": task.id})
    thread = task.state["thread_id"]
    await svc.event({"method": "item/agentMessage/delta", "params": {"threadId": thread, "itemId": "answer", "delta": "正在测试"}})
    await svc.event({"method": "item/tool/requestUserInput", "id": 99, "params": {"threadId": thread, "questions": [{"id": "q", "question": "选哪个"}]}})
    assert task.state["status"] == "waiting"
    data = await svc.dispatch({"op": "view", "task": task.id})
    assert data["transcript"][-1]["text"] == "正在测试"
    await svc.dispatch({"op": "answer", "task": task.id, "request_id": 99, "response": {"q": "方案一"}})
    assert svc.codex.writes[-1] == {"id": 99, "result": {"answers": {"q": {"answers": ["方案一"]}}}}
    with pytest.raises(DeckError, match="结束"):
        await svc.dispatch({"op": "answer", "task": task.id, "request_id": 99, "response": {"q": "重复回答"}})
    await svc.event({"method": "item/commandExecution/requestApproval", "id": 100, "params": {"threadId": thread, "command": "git push"}})
    await svc.dispatch({"op": "answer", "task": task.id, "request_id": 100, "response": "decline"})
    assert svc.codex.writes[-1]["result"] == {"decision": "decline"}
    await svc.flush()
    assert (task.local / "transcript.json").exists()


async def test_disconnect_and_manual_resume_preserve_session(project):
    task = project.add("T001", "one")
    svc = service(project)
    await svc.dispatch({"op": "start", "task": task.id})
    thread = task.state["thread_id"]
    await svc.event({"method": "taskdeck/disconnected", "params": {"reason": "test disconnect"}})
    assert task.state["status"] == "disconnected"
    # Reading the disconnected task does not resume it.
    fresh = service(project)
    await fresh.dispatch({"op": "view", "task": task.id})
    assert fresh.codex is None
    await fresh.dispatch({"op": "start", "task": task.id})
    assert task.state["thread_id"] == thread
    assert not any(m == "thread/start" for m, p in fresh.codex.calls)


async def test_stale_turn_completion_cannot_stop_new_turn(project):
    task = project.add("T001", "one")
    svc = service(project)
    await svc.dispatch({"op": "start", "task": task.id})
    await svc.event({"method": "turn/completed", "params": {"threadId": task.state["thread_id"], "turn": {"id": "old-turn", "status": "failed"}}})
    assert task.state["status"] == "running"
