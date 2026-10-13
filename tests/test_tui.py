from pathlib import Path

from textual.widgets import DataTable, Input, TextArea

from taskdeck.tui import Conversation, TaskDeck


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
