from pathlib import Path

from textual.widgets import Button, DataTable, Input, TabbedContent, TextArea

from taskdeck.tui import CompletionModal, TaskDeck, WorktreeTrustModal
from taskdeck.trust import set_worktree_trust, worktree_trust_enabled


async def test_mouse_keyboard_search_do_not_start_tasks(project):
    project.add("T001", "构建基础")
    project.add("T002", "数据库实现", ["T001"])
    app = TaskDeck(project)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        table = app.query_one("#tasks", DataTable)
        assert table.row_count == 2
        assert app.selected == "T001"
        await pilot.press("down")
        await pilot.pause()
        assert app.selected == "T002"
        assert "T001" in app.query_one("#deps", TextArea).text
        assert not app.query_one(TabbedContent).get_tab("codex-tab").display
        await pilot.click("#search")
        await pilot.press("T", "0", "0", "1")
        await pilot.pause()
        # Search includes dependency IDs, so T002 also matches T001.
        assert table.row_count == 2
        app.query_one("#search", Input).value = "T002"
        await pilot.pause()
        assert table.row_count == 1
        assert not project.socket.exists()
        assert not project.task("T001").local.exists()


async def test_codex_tab_is_first_and_only_visible_for_existing_session(project, monkeypatch):
    from taskdeck.terminal import CodexPane
    a = project.add("T001", "已开始")
    project.add("T002", "尚未开始")
    a.save(status="idle", thread_id="saved-thread")
    opened = []
    async def select_task(self, ident):
        self.ident = ident
        opened.append(ident)
    monkeypatch.setattr(CodexPane, "select_task", select_task)
    app = TaskDeck(project)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        tabs = app.query_one(TabbedContent)
        assert tabs.get_tab("codex-tab").display
        assert tabs.get_tab("codex-tab").region.x < tabs.get_tab("spec-tab").region.x
        await pilot.click(app.query_one(TabbedContent).get_tab("codex-tab"))
        await pilot.pause()
        assert tabs.active == "codex-tab"
        assert opened and set(opened) == {"T001"}
        assert len(app.screen_stack) == 1
        table = app.query_one("#tasks", DataTable)
        table.focus()
        await pilot.press("down")
        await pilot.pause()
        assert app.selected == "T002"
        assert not tabs.get_tab("codex-tab").display
        assert tabs.active == "spec-tab"
        await pilot.press("up")
        await pilot.pause()
        assert tabs.get_tab("codex-tab").display
        assert a.state["thread_id"] == "saved-thread"
        assert not project.socket.exists()


async def test_native_tab_keeps_task_clients_and_owns_dashboard_shortcut_keys(project, monkeypatch, tmp_path):
    import sys
    import taskdeck.terminal as native
    from taskdeck.terminal import CodexPane, NativeTerminal, screen_text
    for ident in ("T001", "T002"):
        project.add(ident, ident).save(status="idle", thread_id="thread-" + ident)
    attached = []
    def attach(project, request):
        assert request["op"] == "attach"
        ident = request["task"]
        attached.append(ident)
        captured = tmp_path / (ident + ".keys")
        script = "import os,tty; tty.setraw(0); print('READY',flush=True); f=open(" + repr(str(captured)) + ", 'ab', buffering=0)\nwhile True: f.write(os.read(0,100))"
        return {"command": [sys.executable, "-c", script], "cwd": str(tmp_path)}
    monkeypatch.setattr(native, "ensure_service", lambda project: None)
    monkeypatch.setattr(native, "rpc", attach)
    app = TaskDeck(project)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        await pilot.click(app.query_one(TabbedContent).get_tab("codex-tab"))
        pane = app.query_one(CodexPane)
        for _ in range(50):
            if "T001" in pane.terminals and pane.terminals["T001"].session and "READY" in screen_text(pane.terminals["T001"].session.screen).plain:
                break
            await pilot.pause(0.05)
        first = pane.terminals["T001"]
        await pane.reconnect_for_trust()
        assert pane.terminals["T001"] is first
        assert attached == ["T001"]
        assert isinstance(app.focused, NativeTerminal)
        await pilot.press("n", "s", "slash", "enter", "escape")
        await pilot.pause(0.15)
        assert (tmp_path / "T001.keys").read_bytes() == b"ns/\r\x1b"
        assert len(app.screen_stack) == 1
        await pilot.press("ctrl+g", "down")
        for _ in range(50):
            if "T002" in pane.terminals:
                break
            await pilot.pause(0.05)
        assert pane.ident == "T002"
        assert not first.display
        app.query_one("#tasks", DataTable).focus()
        await pilot.press("up")
        await pilot.pause(0.15)
        assert pane.terminals["T001"] is first
        assert first.display and first.session.proc.poll() is None
        assert sorted(attached) == ["T001", "T002"]
        assert not project.socket.exists()


async def test_trust_button_enable_cancel_disable_fits_empty_80_column_dashboard(project, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(project.root.parent / "codex-home"))
    monkeypatch.setattr("taskdeck.tui.ensure_service", lambda _: (_ for _ in ()).throw(AssertionError("must not start Codex")))
    app = TaskDeck(project)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        button = app.query_one("#trust-worktrees", Button)
        assert str(button.label) == "目录信任：关"
        assert button.region.x >= 0 and button.region.right <= 80
        assert button.region.y >= 0 and button.region.bottom <= 24
        await pilot.click("#trust-worktrees")
        await pilot.pause()
        assert isinstance(app.screen, WorktreeTrustModal)
        for ident in ("trust-box", "confirm-trust", "cancel-trust"):
            region = app.screen.query_one("#" + ident).region
            assert region.x >= 0 and region.right <= 80
            assert region.y >= 0 and region.bottom <= 24
        await pilot.press("escape")
        await pilot.pause()
        assert not worktree_trust_enabled(project)
        assert not (project.runtime / "trust-worktrees.json").exists()
        await pilot.click("#trust-worktrees")
        await pilot.pause()
        await pilot.click("#confirm-trust")
        await app.workers.wait_for_complete()
        assert worktree_trust_enabled(project)
        assert str(button.label) == "目录信任：开"
        assert button.variant == "success"
        await pilot.click("#trust-worktrees")
        await pilot.pause()
        assert str(app.screen.query_one("#confirm-trust", Button).label) == "关闭自动信任"
        await pilot.click("#confirm-trust")
        await app.workers.wait_for_complete()
        assert not worktree_trust_enabled(project)
        assert str(button.label) == "目录信任：关"
        assert not project.socket.exists()
        assert project.scan() == {}


async def test_trust_button_shows_persisted_state_and_refreshes_external_changes(project, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(project.root.parent / "codex-home"))
    task = project.add("T001", "未开始的任务")
    set_worktree_trust(project, True)
    app = TaskDeck(project)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        button = app.query_one("#trust-worktrees", Button)
        assert str(button.label) == "目录信任：开"
        set_worktree_trust(project, False)
        await app.reload()
        assert str(button.label) == "目录信任：关"
        assert not task.local.exists()
        assert not project.socket.exists()


async def test_trust_ui_preserves_off_state_when_project_is_explicitly_untrusted(project, monkeypatch):
    import json
    home = project.root.parent / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    (home / "config.toml").write_text(f'[projects.{json.dumps(str(project.root))}]\ntrust_level="untrusted"\n')
    app = TaskDeck(project)
    messages = []
    monkeypatch.setattr(app, "notify", lambda message, **kwargs: messages.append(str(message)))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.click("#trust-worktrees")
        await pilot.pause()
        await pilot.click("#confirm-trust")
        await app.workers.wait_for_complete()
        assert any("untrusted" in message for message in messages)
        assert not worktree_trust_enabled(project)
        button = app.query_one("#trust-worktrees", Button)
        assert str(button.label) == "目录信任：关" and not button.disabled
        assert not project.socket.exists()


async def test_trust_dialog_can_open_during_background_refresh(project, monkeypatch):
    import asyncio
    import threading
    from taskdeck.service import snapshot
    entered, release = threading.Event(), threading.Event()
    app = TaskDeck(project)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        def delayed_snapshot(project):
            entered.set()
            assert release.wait(5)
            return snapshot(project)
        monkeypatch.setattr("taskdeck.tui.snapshot", delayed_snapshot)
        refresh = asyncio.create_task(app.reload())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await pilot.click("#trust-worktrees")
            await pilot.pause()
            assert isinstance(app.screen, WorktreeTrustModal)
        finally:
            release.set()
            await refresh
        assert isinstance(app.screen, WorktreeTrustModal)
        assert not project.socket.exists()


async def test_completion_button_visible_and_clickable_in_80_column_terminal(project):
    task = project.add("T001", "已交付任务")
    (task.folder / "task.yaml").write_text("id: T001\ntitle: 已交付任务\nsource_status: 已完成\n")
    app = TaskDeck(project)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        for ident in ("start", "complete", "open-pr", "sync"):
            region = app.query_one("#" + ident, Button).region
            assert region.x >= 0 and region.right <= 80
            assert region.y >= 0 and region.bottom <= 24
        button = app.query_one("#complete", Button)
        assert not button.disabled
        await pilot.click("#complete")
        await pilot.pause()
        assert isinstance(app.screen, CompletionModal)
        assert app.screen.managed_task.id == "T001"
        await pilot.press("escape")
        await pilot.pause()
        app.query_one("#tasks", DataTable).focus()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CompletionModal)
        assert not project.socket.exists()


async def test_normal_session_can_open_completion_and_has_no_manual_pr_button(project):
    task = project.add("T003", "普通开发任务")
    task.save(status="idle", thread_id="existing-session")
    app = TaskDeck(project)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert not app.query("#create-pr")
        assert not app.query_one("#complete", Button).disabled
        await pilot.click("#complete")
        await pilot.pause()
        assert isinstance(app.screen, CompletionModal)
        assert app.screen.managed_task.id == "T003"
        assert task.state["thread_id"] == "existing-session"
