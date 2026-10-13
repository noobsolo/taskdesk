from pathlib import Path

import pytest

from taskdeck.core import DeckError, git
from taskdeck.importer import import_markdown
from taskdeck.tui import TaskDeck
from textual.widgets import Button, DataTable, TextArea


def source(project, ident, title="任务", deps="无", status="待开始"):
    folder = project.root / "docs/tasks"
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / f"{ident}-source.md"
    file.write_text(f"# {ident} {title}\n\n| 状态 | {status} |\n| 依赖 | {deps} |\n| 被依赖 | T999 |\n\n## 验证与完成条件\n- [ ] 真实测试通过\n")
    return file


def test_import_preserves_source_text_and_dependencies_without_running(project):
    a = source(project, "T001", status="已完成")
    b = source(project, "T002", deps="[T001 任务](T001-source.md)")
    result = import_markdown(project, "docs/tasks", dry_run=True)
    assert result["found"] == 2
    assert project.scan() == {}
    result = import_markdown(project, "docs/tasks")
    assert result["imported"] == 2
    assert project.task("T001").description() == a.read_text()
    assert project.task("T002").description() == b.read_text()
    assert project.task("T002").deps == ["T001"]
    rows = project.snapshot()["tasks"]
    assert rows[0]["status"] == "unverified"
    assert rows[1]["status"] == "blocked"
    assert not project.socket.exists()
    assert not any(task.local.exists() for task in project.scan().values())


def test_import_is_idempotent_and_does_not_overwrite_user_edits(project):
    source(project, "T001")
    import_markdown(project)
    task = project.task("T001")
    (task.folder / "description.md").write_text("用户修改后的任务")
    task.save(thread_id="preserved-session")
    result = import_markdown(project)
    assert result["imported"] == 0 and result["skipped"] == 1
    assert task.description() == "用户修改后的任务"
    assert task.state["thread_id"] == "preserved-session"


def test_bad_dependency_and_cycles_fail_before_writing(project):
    a = source(project, "T001", deps="T002")
    with pytest.raises(DeckError, match="不存在"):
        import_markdown(project)
    assert project.scan() == {}
    source(project, "T002", deps="T001")
    with pytest.raises(DeckError, match="循环"):
        import_markdown(project)
    assert project.scan() == {}


async def test_empty_screen_explains_legacy_import_and_disables_start(project):
    source(project, "T001")
    app = TaskDeck(project)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert "1 份任务文档" in app.query_one("#spec", TextArea).text
        assert app.query_one("#start", Button).disabled
        await pilot.click("#import")
        await pilot.pause(0.4)
        assert app.query_one("#tasks", DataTable).row_count == 1
        assert not project.socket.exists()


def test_completed_document_does_not_unlock_until_commit_confirmed(project):
    source(project, "T001", status="已完成")
    source(project, "T002", deps="T001")
    git(project.root, "add", "docs/tasks")
    git(project.root, "commit", "-m", "existing task delivery")
    import_markdown(project)
    candidate = project.task("T001").definition["completion_candidate"]
    assert project.blockers(project.task("T002"))
    project.record_completion("T001", candidate)
    assert not project.blockers(project.task("T002"))


async def test_shared_dependencies_are_expanded_once(project):
    project.add("T001", "one")
    project.add("T002", "two")
    for index in range(3, 21):
        project.add(f"T{index:03d}", f"task {index}", [f"T{index-1:03d}", f"T{index-2:03d}"])
    app = TaskDeck(project)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause()
        app.query_one("#tasks", DataTable).move_cursor(row=19)
        await pilot.pause()
        text = app.query_one("#deps", TextArea).text
        assert "已在上方展开" in text
        assert len(text.splitlines()) < 60
