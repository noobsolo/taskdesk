import json

import pytest

from taskdeck.cli import main
from taskdeck.core import DeckError, Project, git
from taskdeck.trust import _eligible_worktree, inherit_worktree_trust, set_worktree_trust, worktree_trust_enabled


@pytest.fixture
def managed(project, tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    task = project.add("T001", "trust test")
    # Exercise quoted config keys and CJK in a real managed worktree path.
    folder = task.folder.with_name('任务 "quoted"')
    task.folder.rename(folder)
    task = project.task(task.id)
    path = project.ensure_worktree(task)
    return project, task, path, home / "config.toml"


def trust_config(file, path, level):
    file.write_text(f'[projects.{json.dumps(str(path), ensure_ascii=False)}]\ntrust_level = "{level}"\n')


def test_opt_in_is_local_and_only_allows_the_managed_folder(managed):
    project, task, path, config = managed
    project.config["trust_worktrees"] = True
    assert not worktree_trust_enabled(project)
    assert _eligible_worktree(project, task, path) is None
    set_worktree_trust(project, True)
    assert _eligible_worktree(project, task, path) == path
    assert not config.exists()
    assert worktree_trust_enabled(Project.discover(path))
    set_worktree_trust(project, False)
    assert _eligible_worktree(project, task, path) is None


@pytest.mark.parametrize("level", ["trusted", "untrusted"])
def test_explicit_worktree_choice_is_preserved(managed, level):
    project, task, path, config = managed
    set_worktree_trust(project, True)
    trust_config(config, str(path) + "/", level)
    before = config.read_bytes()
    assert _eligible_worktree(project, task, path) is None
    assert config.read_bytes() == before


def test_explicit_root_distrust_prevents_enable_and_later_inheritance(managed):
    project, task, path, config = managed
    trust_config(config, project.root, "untrusted")
    with pytest.raises(DeckError, match="untrusted"):
        set_worktree_trust(project, True)
    assert not worktree_trust_enabled(project)
    config.unlink()
    set_worktree_trust(project, True)
    trust_config(config, project.root, "untrusted")
    assert _eligible_worktree(project, task, path) is None
    set_worktree_trust(project, False)


def test_opt_in_does_not_follow_changed_project_or_codex_home(managed, monkeypatch):
    project, task, path, config = managed
    set_worktree_trust(project, True)
    ident = project.config["project_id"]
    project.config["project_id"] = "a different project"
    assert _eligible_worktree(project, task, path) is None
    project.config["project_id"] = ident
    monkeypatch.setenv("CODEX_HOME", str(config.parent.with_name("other-home")))
    assert _eligible_worktree(project, task, path) is None


def test_state_cannot_redirect_trust_to_another_managed_worktree(managed):
    project, task, path, _ = managed
    other = project.add("T002", "another task")
    other_path = project.ensure_worktree(other)
    set_worktree_trust(project, True)
    task.save(worktree=str(other_path))
    assert _eligible_worktree(project, task, other_path) is None
    assert _eligible_worktree(project, task, project.root) is None


def test_managed_path_must_be_a_registered_worktree_of_the_same_repo(managed):
    project, task, path, _ = managed
    set_worktree_trust(project, True)
    git(project.root, "worktree", "remove", str(path))
    path.mkdir()
    git(path, "init", "-b", "main")
    assert _eligible_worktree(project, task, path) is None


def test_symlink_to_worktree_outside_task_root_does_not_inherit(managed, tmp_path):
    project, task, path, _ = managed
    set_worktree_trust(project, True)
    moved = tmp_path / "moved-worktree"
    git(project.root, "worktree", "move", str(path), str(moved))
    path.symlink_to(moved, target_is_directory=True)
    assert _eligible_worktree(project, task, path) is None


def test_invalid_codex_config_fails_closed_and_can_still_disable(managed):
    project, task, path, config = managed
    set_worktree_trust(project, True)
    config.write_text("not valid TOML = [")
    with pytest.raises(DeckError, match="无法核对"):
        _eligible_worktree(project, task, path)
    set_worktree_trust(project, False)
    assert _eligible_worktree(project, task, path) is None


def test_cli_enable_status_disable_never_connects_or_starts_work(managed, monkeypatch, capsys):
    project, task, path, config = managed
    monkeypatch.setattr("taskdeck.cli.ensure_service", lambda _: pytest.fail("trust must not start Codex"))
    before = task.state
    for action, enabled in [("status", False), ("enable", True), ("status", True), ("disable", False)]:
        assert main(["-C", str(path), "trust-worktrees", action]) == 0
        assert worktree_trust_enabled(project) is enabled
        assert f'"enabled": {str(enabled).lower()}' in capsys.readouterr().out
    assert task.state == before
    assert not project.socket.exists()
    assert not config.exists()


@pytest.mark.parametrize("scenario", ["write", "untrusted", "conflict", "other-home", "no-version", "disabled-during-read"])
async def test_native_config_write_requires_current_authorization_and_version(managed, monkeypatch, scenario):
    project, task, path, config = managed
    set_worktree_trust(project, True)
    calls = []
    projects = {str(project.root): {"trust_level": "trusted"}}
    if scenario == "untrusted":
        projects[str(path)] = {"trust_level": "untrusted"}
    read = {"config": {"projects": projects}, "layers": [
        {"name": {"type": "user", "file": str(config)}, "version": "before-change", "config": {}}]}
    if scenario == "other-home":
        read["layers"][0]["name"]["file"] = str(config.parent.with_name("other-home") / "config.toml")
    if scenario == "no-version":
        read["layers"][0].pop("version")

    class Socket:
        async def send(self, message):
            request = json.loads(message)
            calls.append(request)
            method = request["method"]
            if method == "config/read" and scenario == "disabled-during-read":
                set_worktree_trust(project, False)
            if "id" in request:
                self.response = {"id": request["id"], "result": read if method == "config/read" else {}}
                if method == "config/value/write" and scenario == "conflict":
                    self.response = {"id": request["id"], "error": {"message": "version conflict"}}

        async def recv(self):
            return json.dumps(self.response)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr("taskdeck.trust.unix_connect", lambda *a, **kw: Socket())
    connection = {"cwd": str(path), "endpoint": "unix://" + str(project.codex_socket)}
    if scenario in {"conflict", "other-home", "no-version"}:
        with pytest.raises(DeckError):
            await inherit_worktree_trust(project, task, connection)
    else:
        await inherit_worktree_trust(project, task, connection)
    writes = [c for c in calls if c["method"] == "config/value/write"]
    if scenario in {"untrusted", "other-home", "no-version", "disabled-during-read"}:
        assert not writes
    else:
        assert len(writes) == 1
        assert writes[0]["params"] == {
            "filePath": str(config), "expectedVersion": "before-change",
            "keyPath": f'projects.{json.dumps(str(path), ensure_ascii=False)}.trust_level',
            "value": "trusted", "mergeStrategy": "upsert"}
    assert not config.exists()


async def test_unknown_remote_endpoint_cannot_receive_trust_write(managed):
    project, task, path, _ = managed
    set_worktree_trust(project, True)
    with pytest.raises(DeckError, match="接入端点"):
        await inherit_worktree_trust(project, task, {"cwd": str(path), "endpoint": "unix:///another.sock"})
