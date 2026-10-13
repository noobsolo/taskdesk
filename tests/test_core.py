import json
import shutil

import pytest
import yaml

from taskdeck.core import DeckError, Project, git, read_json


def test_init_is_idempotent_and_does_not_launch_tasks(project):
    assert project.scan() == {}
    before = (project.root / ".taskctl.yaml").read_text()
    again = Project.init(project.root, tasks_dir="elsewhere", base="wrong")
    assert again.root == project.root
    assert (project.root / ".taskctl.yaml").read_text() == before
    assert not project.socket.exists()


def test_folder_id_dependency_and_runtime_ignore(project):
    a = project.add("T001", "构建基础")
    b = project.add("T002", "数据库", ["T001"])
    assert b.deps == [a.id]
    assert project.snapshot()["tasks"][1]["status"] == "blocked"
    a.save(status="idle", thread_id="thread-one")
    assert json.loads((a.local / "session.json").read_text())["thread_id"] == "thread-one"
    status = git(project.root, "status", "--untracked-files=all", "--porcelain").stdout
    assert "task.yaml" in status and ".local" not in status


def test_validation_duplicate_missing_cycle(project):
    a = project.add("T001", "one")
    b = project.add("T002", "two", ["T001"])
    (b.folder / "task.yaml").write_text("id: T001\ntitle: duplicate\n")
    with pytest.raises(DeckError, match="重复"):
        project.scan()
    (b.folder / "task.yaml").write_text("id: T002\ntitle: two\ndepends_on: [T999]\n")
    with pytest.raises(DeckError, match="不存在"):
        project.scan()
    (b.folder / "task.yaml").write_text("id: T002\ntitle: two\ndepends_on: [T001]\n")
    (a.folder / "task.yaml").write_text("id: T001\ntitle: one\ndepends_on: [T002]\n")
    with pytest.raises(DeckError, match="循环"):
        project.scan()


def test_dependency_requires_commit_in_actual_worktree(project):
    a = project.add("T001", "one")
    b = project.add("T002", "two", ["T001"])
    old_head = git(project.root, "rev-parse", "HEAD").stdout.strip()
    stale = project.root.parent / "stale"
    git(project.root, "worktree", "add", "-b", "old", str(stale), old_head)
    (project.root / "upstream.py").write_text("ready = True\n")
    git(project.root, "add", "upstream.py")
    git(project.root, "commit", "-m", "upstream implementation")
    commit = git(project.root, "rev-parse", "HEAD").stdout.strip()
    a.save(pr={"state": "open"}, status="idle")
    assert project.blockers(b)
    project.record_completion("T001", commit)
    assert not project.blockers(b)
    assert project.blockers(b, ref="refs/heads/old")
    path = project.ensure_worktree(b)
    assert (path / "upstream.py").exists()
    assert git(path, "rev-parse", "HEAD").stdout.strip() == commit


def test_common_git_directory_and_two_projects_are_isolated(project, tmp_path):
    a = project.add("T001", "one")
    path = project.ensure_worktree(a)
    (path / "nested").mkdir()
    assert Project.discover(path / "nested").root == project.root
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-b", "main")
    second = Project.init(other)
    second.add("T001", "same ID in different repo")
    assert second.socket != project.socket
    assert second.scan()["T001"].folder != a.folder


def test_renamed_folder_preserves_session_and_repairs_worktree(project):
    a = project.add("T001", "one")
    path = project.ensure_worktree(a)
    a.save(thread_id="existing-session")
    renamed = a.folder.with_name("T001-renamed")
    a.folder.rename(renamed)
    task = project.task("T001")
    repaired = project.ensure_worktree(task)
    assert repaired == renamed / ".local" / "worktree"
    assert task.state["thread_id"] == "existing-session"
    assert git(repaired, "symbolic-ref", "--short", "HEAD").stdout.strip() == "task/T001"


def test_worktree_preserves_local_edits_and_missing_worktree_fails_closed(project):
    a = project.add("T001", "one")
    path = project.ensure_worktree(a)
    (path / "README.md").write_text("user edit")
    assert project.ensure_worktree(a) == path
    assert (path / "README.md").read_text() == "user edit"
    a.save(thread_id="saved")
    shutil.rmtree(path)
    with pytest.raises(DeckError, match="缺失"):
        project.ensure_worktree(a)


def test_path_escape_and_symlink_manifests_rejected(project, tmp_path):
    project.config["tasks_dir"] = "../escape"
    (project.root / ".taskctl.yaml").write_text(yaml.safe_dump(project.config))
    with pytest.raises(DeckError, match="路径"):
        Project(project.root)


def test_large_task_graph(project):
    for index in range(1, 121):
        project.add(f"T{index:03d}", f"任务 {index}", [] if index == 1 else [f"T{index-1:03d}"])
    rows = project.snapshot()["tasks"]
    assert len(rows) == 120
    assert sum(r["status"] == "ready" for r in rows) == 1
    assert sum(r["status"] == "blocked" for r in rows) == 119
    assert not project.socket.exists()
