import pytest

from taskdeck.core import DeckError, git
from taskdeck.github import GitHub


def client(project):
    git(project.root, "remote", "add", "origin", "git@github.com:example/demo.git")
    return GitHub(project)


def test_open_pr_does_not_unlock_dependency_and_merge_must_be_in_base(project, monkeypatch):
    a = project.add("T001", "one")
    b = project.add("T002", "two", ["T001"])
    project.ensure_worktree(a)
    github = client(project)
    assert github.repo == "demo"
    pr = {"number": 1, "html_url": "https://github.com/example/demo/pull/1", "state": "open", "merged_at": None}
    monkeypatch.setattr(github, "find", lambda branch: pr)
    github.sync(a)
    assert project.blockers(b)
    pr.update(state="closed", merged_at="2026-10-13", merge_commit_sha="a" * 40)
    github.sync(a)
    assert a.state["pr"]["state"] == "merged"
    assert project.blockers(b)
    pr["merge_commit_sha"] = git(project.root, "rev-parse", "HEAD").stdout.strip()
    github.sync(a)
    assert not project.blockers(b)


def test_pr_create_requires_commits_clean_tree_and_delivery(project, monkeypatch):
    task = project.add("T001", "one")
    path = project.ensure_worktree(task)
    github = client(project)
    monkeypatch.setattr(github, "find", lambda branch: None)
    with pytest.raises(DeckError, match="新的提交"):
        github.create(task)
    (path / "feature.py").write_text("implemented = True\n")
    with pytest.raises(DeckError, match="未提交"):
        github.create(task)
    git(path, "add", ".")
    git(path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "feature")
    with pytest.raises(DeckError, match="delivery"):
        github.create(task)


def test_existing_pr_is_reused_after_push(project, monkeypatch):
    task = project.add("T001", "one")
    path = project.ensure_worktree(task)
    github = client(project)
    (path / "feature.py").write_text("implemented = True\n")
    git(path, "add", ".")
    git(path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "feature")
    pr = {"number": 9, "html_url": "https://github.com/example/demo/pull/9", "state": "open", "merged_at": None}
    monkeypatch.setattr(github, "find", lambda branch: pr)
    calls = []
    from taskdeck import github as module
    original_git = module.git
    def fake_git(root, *args, **kwargs):
        if args[0] == "push":
            calls.append(args)
            return None
        return original_git(root, *args, **kwargs)
    monkeypatch.setattr(module, "git", fake_git)
    monkeypatch.setattr(github, "api", lambda *args, **kwargs: pytest.fail("existing PR must not be recreated"))
    result = github.create(task, body="Implementation and validation results")
    assert result["number"] == 9
    assert len(calls) == 1
