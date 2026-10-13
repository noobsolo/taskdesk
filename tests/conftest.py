import subprocess

import pytest

from taskdeck.core import Project


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project with spaces"
    root.mkdir()
    def git(*args):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    git("init", "-b", "main")
    git("config", "user.name", "Taskdeck Test")
    git("config", "user.email", "taskdeck@example.invalid")
    (root / "README.md").write_text("Test repository\n")
    git("add", ".")
    git("commit", "-m", "initial")
    return Project.init(root)
