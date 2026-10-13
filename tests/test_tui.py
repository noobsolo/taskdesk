from pathlib import Path

from textual.widgets import Button, DataTable, Input, TextArea

from taskdeck.tui import CompletionModal, Conversation, TaskDeck


async def test_mouse_keyboard_search_and_enter_do_not_start_tasks(project):
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
        await pilot.click("#enter")
        await pilot.pause()
        assert isinstance(app.screen, Conversation)
        assert app.screen.ident == "T002"
        await pilot.press("escape")
        await pilot.pause()
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


async def test_new_task_modal_and_small_terminal(project):
    app = TaskDeck(project)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.click("#new")
        await pilot.pause()
        app.screen.query_one("#new-id", Input).value = "T001"
        app.screen.query_one("#new-title", Input).value = "新任务"
        await pilot.click("#create")
        await pilot.pause()
        assert project.task("T001").title == "新任务"
        assert app.query_one("#tasks", DataTable).row_count == 1
        assert not project.task("T001").local.exists()


async def test_completion_button_visible_and_clickable_in_80_column_terminal(project):
    task = project.add("T001", "已交付任务")
    (task.folder / "task.yaml").write_text("id: T001\ntitle: 已交付任务\nsource_status: 已完成\n")
    app = TaskDeck(project)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        for ident in ("new", "start", "complete", "enter", "show-diff", "create-pr", "open-pr", "sync"):
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
