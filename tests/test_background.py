import asyncio
from pathlib import Path
import sys
import time

import yaml

from taskdeck.core import Project
from taskdeck.service import ensure_service, online, rpc, view
from taskdeck.tui import TaskDeck
from textual.widgets import TabbedContent


async def test_real_background_process_and_tui_steering(project):
    project.add("T001", "会话交互验证")
    project.add("T002", "第二个并行会话")
    project.config["codex_command"] = [sys.executable, str(Path(__file__).with_name("fake_app_server.py"))]
    (project.root / ".taskctl.yaml").write_text(yaml.safe_dump(project.config))
    project = Project(project.root)
    await asyncio.to_thread(ensure_service, project)
    try:
        assert online(project)
        assert not (project.runtime / "codex.log").exists()
        await asyncio.gather(*(asyncio.to_thread(rpc, project, {"op": "start", "task": ident}) for ident in ("T001", "T002")))
        original = project.task("T001").state["thread_id"]
        assert original != project.task("T002").state["thread_id"]
        app = TaskDeck(project)
        async with app.run_test(size=(130, 40)) as pilot:
            await pilot.pause()
            assert app.query_one(TabbedContent).get_tab("codex-tab").display
            await asyncio.to_thread(rpc, project, {"op": "send", "task": "T001", "text": "先检查测试"})
            assert "收到调整：先检查测试" in view(project, "T001")["transcript"][-1]["text"]
            await asyncio.to_thread(rpc, project, {"op": "stop", "task": "T001"})
            assert project.task("T001").state["status"] == "interrupted"
        # Closing the dashboard leaves the independent service and session alive.
        assert online(project)
        await asyncio.to_thread(rpc, project, {"op": "send", "task": "T001", "text": "继续本任务"})
        assert project.task("T001").state["thread_id"] == original
        assert project.task("T002").state["status"] == "running"
    finally:
        await asyncio.to_thread(rpc, project, {"op": "shutdown"})
        for _ in range(50):
            if not project.socket.exists():
                break
            await asyncio.sleep(0.05)
