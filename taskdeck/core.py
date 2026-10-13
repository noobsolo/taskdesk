from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

import yaml


class DeckError(Exception):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def command(args, cwd, timeout=30, check=True):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise DeckError(str(e)) from e
    if check and p.returncode:
        raise DeckError(p.stderr.strip() or p.stdout.strip() or f"命令失败：{args[0]}")
    return p


def git(root, *args, check=True, timeout=30):
    return command(["git", *args], root, timeout, check)


def read_json(path, default=None):
    if not path.exists():
        return {} if default is None else default
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError) as e:
        raise DeckError(f"无法读取 {path}: {e}") from e


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_yaml(path):
    try:
        value = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as e:
        raise DeckError(f"无法读取 {path}: {e}") from e
    if not isinstance(value, dict):
        raise DeckError(f"{path} 必须是 YAML 对象")
    return value


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise DeckError(f"路径必须位于项目内：{relative}")
    return path


@contextlib.contextmanager
def lock(path, blocking=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise DeckError("另一个 taskdeck 服务正在运行")
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


LABELS = {"ready": "可开始", "blocked": "被阻塞", "preparing": "准备中",
          "running": "开发中", "waiting": "等你处理", "idle": "等你继续",
          "interrupted": "已中断", "disconnected": "需恢复", "failed": "失败",
          "done": "已入基线"}
LABELS["unverified"] = "待核对"
ACTIVE = {"preparing", "running", "waiting"}


class Task:
    def __init__(self, project, folder, definition):
        self.project, self.folder, self.definition = project, folder, definition
        self.id = definition["id"]
        self.title = definition["title"]
        self.deps = definition.get("depends_on", [])
        self.local = folder / ".local"
        self.state_path = self.local / "state.json"

    @property
    def state(self):
        return read_json(self.state_path)

    def save(self, **updates):
        with lock(self.local / "state.lock"):
            state = self.state
            state.update(updates, updated_at=now())
            write_json(self.state_path, state)
            if state.get("thread_id"):
                write_json(self.local / "session.json", {"thread_id": state["thread_id"]})
        return state

    def event(self, kind, **data):
        self.local.mkdir(parents=True, exist_ok=True)
        with (self.local / "events.jsonl").open("a") as f:
            f.write(json.dumps({"time": now(), "kind": kind, **data}, ensure_ascii=False) + "\n")

    def description(self):
        file = self.folder / "description.md"
        return file.read_text() if file.exists() else ""


class Project:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.config = read_yaml(self.root / ".taskctl.yaml")
        self.common = Path(git(self.root, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
        self.runtime = self.common / "taskdeck"
        self.task_root = inside(self.root, self.config.get("tasks_dir", "tasks"))
        if self.task_root.is_relative_to(self.root / ".git"):
            raise DeckError("任务目录不能位于 .git 中")
        self.base = self.config.get("target_branch", "main")
        if not isinstance(self.base, str) or git(self.root, "check-ref-format", "--branch", self.base, check=False).returncode:
            raise DeckError("target_branch 不是合法分支名")
        digest = hashlib.sha256(str(self.common).encode()).hexdigest()[:20]
        # macOS Unix sockets have a short path limit. User-private fixed parent.
        self.socket = Path(tempfile.gettempdir()) / f"taskdeck-{os.getuid()}" / (digest + ".sock")

    @classmethod
    def discover(cls, cwd=None):
        cwd = Path(cwd or Path.cwd()).resolve()
        result = git(cwd, "rev-parse", "--show-toplevel", check=False)
        if result.returncode:
            raise DeckError("当前目录不在 Git 工作目录中")
        root = Path(result.stdout.strip())
        common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
        registry = read_json(common / "taskdeck" / "project.json")
        saved = Path(registry.get("root", str(root)))
        if (saved / ".taskctl.yaml").exists():
            candidate = cls(saved)
            if candidate.common == common:
                return candidate
        if (root / ".taskctl.yaml").exists():
            project = cls(root)
            project.register()
            return project
        raise DeckError("项目未初始化，请运行 taskdeck init")

    def register(self):
        write_json(self.runtime / "project.json", {"root": str(self.root)})

    @classmethod
    def init(cls, cwd=None, tasks_dir="tasks", base=None):
        cwd = Path(cwd or Path.cwd()).resolve()
        root = Path(git(cwd, "rev-parse", "--show-toplevel").stdout.strip())
        common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
        if (common / "taskdeck" / "project.json").exists() or (root / ".taskctl.yaml").exists():
            return cls.discover(cwd)
        task_root = inside(root, tasks_dir)
        branch = base or git(root, "symbolic-ref", "--short", "HEAD", check=False).stdout.strip() or "main"
        config = {"version": 1, "project_id": str(uuid.uuid4()), "tasks_dir": tasks_dir,
                  "target_branch": branch, "remote": "origin", "codex_command": ["codex", "app-server"]}
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", tasks_dir) or any(c in tasks_dir for c in "*?["):
            raise DeckError("任务目录仅支持字母、数字、下划线、连字符和路径分隔符")
        if git(root, "check-ref-format", "--branch", branch, check=False).returncode:
            raise DeckError("目标分支名不合法")
        task_root.mkdir(parents=True, exist_ok=True)
        (root / ".taskctl.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
        ignore = root / ".gitignore"
        text = ignore.read_text() if ignore.exists() else ""
        pattern = f"/{tasks_dir.strip('/') }/*/.local/"
        if pattern not in text.splitlines():
            ignore.write_text(text.rstrip() + "\n\n# taskdeck: local sessions and worktrees\n" + pattern + "\n")
        project = cls(root)
        project.register()
        return project

    def scan(self):
        tasks = {}
        if not self.task_root.exists():
            return tasks
        for file in sorted(self.task_root.glob("*/task.yaml")):
            if file.is_symlink() or not file.resolve().is_relative_to(self.task_root):
                raise DeckError(f"任务清单不允许指向目录外：{file}")
            spec = read_yaml(file)
            ident, title = spec.get("id"), spec.get("title")
            if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", ident):
                raise DeckError(f"{file}: id 必须是字母开头的唯一标识")
            if not isinstance(title, str) or not title.strip():
                raise DeckError(f"{ident}: 缺少 title")
            for key in ("depends_on", "acceptance"):
                val = spec.get(key, [])
                if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
                    raise DeckError(f"{ident}: {key} 必须是字符串列表")
            if ident in tasks:
                raise DeckError(f"任务 ID 重复：{ident}")
            tasks[ident] = Task(self, file.parent, spec)
        visiting, visited = set(), set()
        def visit(ident):
            if ident in visiting:
                raise DeckError(f"循环依赖：{ident}")
            if ident in visited:
                return
            visiting.add(ident)
            for dep in tasks[ident].deps:
                if dep not in tasks:
                    raise DeckError(f"{ident} 的依赖不存在：{dep}")
                visit(dep)
            visiting.remove(ident)
            visited.add(ident)
        for ident in tasks:
            visit(ident)
        return tasks

    def task(self, ident):
        tasks = self.scan()
        if ident not in tasks:
            raise DeckError(f"任务不存在：{ident}")
        return tasks[ident]

    def add(self, ident, title, deps=None):
        with lock(self.runtime / "edit.lock"):
            existing = self.scan()
            if ident in existing:
                raise DeckError(f"任务 ID 已存在：{ident}")
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", ident):
                raise DeckError("任务 ID 不合法")
            if not title.strip():
                raise DeckError("任务标题不能为空")
            for dep in deps or []:
                if dep not in existing:
                    raise DeckError(f"依赖不存在：{dep}")
            folder = self.task_root / ident
            if folder.exists():
                raise DeckError(f"目录已存在：{folder}")
            folder.mkdir()
            spec = {"id": ident, "title": title, "depends_on": deps or [], "acceptance": []}
            (folder / "task.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False))
            (folder / "description.md").write_text(f"# {ident} {title}\n\n在这里写任务范围、实现要求和验证方式。\n")
            return self.task(ident)

    def base_ref(self):
        remote = self.config.get("remote", "origin")
        remote_ref = f"refs/remotes/{remote}/{self.base}"
        if git(self.root, "show-ref", "--verify", remote_ref, check=False).returncode == 0:
            return remote_ref
        return f"refs/heads/{self.base}"

    def sync_base(self):
        remote = self.config.get("remote", "origin")
        if remote in git(self.root, "remote").stdout.splitlines():
            git(self.root, "fetch", remote, f"{self.base}:refs/remotes/{remote}/{self.base}", timeout=90)
        git(self.root, "rev-parse", "--verify", self.base_ref() + "^{commit}")

    def has_commit(self, commit, ref=None):
        if not commit or not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{7,64}", commit):
            return False
        return git(self.root, "merge-base", "--is-ancestor", commit, ref or self.base_ref(), check=False).returncode == 0

    def blockers(self, task, tasks=None, ref=None):
        tasks = tasks or self.scan()
        result = []
        for dep in task.deps:
            state = tasks[dep].state
            commit = state.get("completed_commit") or tasks[dep].definition.get("completed_commit")
            if not self.has_commit(commit, ref):
                result.append(f"{dep} 尚未交付到当前基线")
        return result

    def snapshot(self):
        tasks = self.scan()
        rows = []
        # Commit ancestry reused across downstream tasks for large task sets.
        included = {}
        for task in tasks.values():
            state = task.state
            commit = state.get("completed_commit") or task.definition.get("completed_commit")
            included[task.id] = self.has_commit(commit) if commit else False
        for task in tasks.values():
            state = task.state
            blockers = [f"{dep} 尚未交付到当前基线" for dep in task.deps if not included[dep]]
            status = state.get("status", "blocked" if blockers else "ready")
            if not state.get("thread_id") and task.definition.get("source_status") == "已完成":
                status = "unverified"
            if included[task.id]:
                status = "done"
            elif status == "done":
                status = "blocked" if blockers else "idle"
            rows.append({"id": task.id, "title": task.title, "deps": task.deps,
                         "folder": str(task.folder), "state": state, "status": status,
                         "label": LABELS.get(status, status), "blockers": blockers,
                         "unlocks": sum(task.id in t.deps for t in tasks.values()),
                         "included": included[task.id]})
        return {"root": str(self.root), "base": self.base, "tasks": rows}

    def ensure_worktree(self, task):
        state = task.state
        path = task.local / "worktree"
        branch = state.get("branch") or f"task/{task.id}"
        if path.exists():
            # A task folder may have been renamed; repair Git's linked-worktree pointers.
            if state.get("worktree") != str(path):
                git(self.root, "worktree", "repair", str(path))
            common = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
            actual = git(path, "symbolic-ref", "--short", "HEAD").stdout.strip()
            if Path(common) != self.common or actual != branch:
                raise DeckError("任务工作目录与登记的仓库/分支不一致")
        else:
            if state.get("thread_id"):
                raise DeckError("原会话的 worktree 缺失，请先恢复目录；不会另建目录覆盖原状态")
            task.local.mkdir(parents=True, exist_ok=True)
            if git(self.root, "check-ignore", str(path), check=False).returncode:
                raise DeckError("任务 .local/ 未被 Git 忽略，请检查 .gitignore")
            if git(self.root, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0:
                raise DeckError(f"分支 {branch} 已存在但没有任务工作目录，请手动核对")
            git(self.root, "worktree", "add", "-b", branch, str(path), self.base_ref())
        task.save(branch=branch, worktree=str(path))
        blockers = self.blockers(task, ref="refs/heads/" + branch)
        if blockers:
            raise DeckError("工作目录缺少依赖：" + "; ".join(blockers) + "。请先在该 worktree 更新目标分支后重试。")
        return path

    def prompt(self, task, path):
        manifest = yaml.safe_dump(task.definition, allow_unicode=True, sort_keys=False)
        rel = str(task.folder.relative_to(self.root))
        return (f"你正在执行独立任务 {task.id}：{task.title}。\n工作目录：{path}\n"
                "仅实现本任务，核对前置依赖的实际代码和接口，不代做缺失的上游任务。"
                "遵守工作目录中的 AGENTS.md。完成实现及必要验证后，记录真实结果，"
                f"将交付记录写入 {rel}/delivery.md，提交并推送当前分支、创建 PR。"
                f"可以使用 taskdeck pr {task.id} 创建或复用 PR；该命令读取 delivery.md。"
                "如果本分支已有 PR，更新原 PR。不要合并 PR，不要启动其他任务。"
                "失败、缺少凭证或验证未通过时明确报告，不要声称成功。\n\n"
                f"原始任务文件：{task.definition.get('source', rel + '/description.md')}。"
                "任务原文中的相对链接以原始任务文件所在目录解析。\n\n"
                f"任务清单：\n{manifest}\n任务说明：\n{task.description()}")

    def diff(self, ident):
        task = self.task(ident)
        path = task.state.get("worktree")
        if not path or not Path(path).exists():
            return "尚未创建工作目录"
        p = Path(path)
        # Include committed branch changes and current edits; list untracked files separately.
        text = git(p, "diff", self.base_ref() + "...HEAD", "--stat").stdout
        text += git(p, "diff", self.base_ref(), "--", ".").stdout
        text += "\n未跟踪文件：\n" + git(p, "ls-files", "--others", "--exclude-standard").stdout
        return text or "暂无改动"

    def record_completion(self, ident, commit):
        task = self.task(ident)
        full = git(self.root, "rev-parse", "--verify", commit + "^{commit}").stdout.strip()
        if not self.has_commit(full):
            raise DeckError("该提交尚未包含在目标基线中，不能解除依赖")
        if task.state.get("status") in ACTIVE:
            raise DeckError("任务正在运行，不能标记完成")
        task.save(completed_commit=full, status="done")
        task.event("completion", commit=full)
