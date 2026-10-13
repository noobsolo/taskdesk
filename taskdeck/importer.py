"""Import existing Markdown task documents without executing or rewriting them."""
from __future__ import annotations

import hashlib
import contextlib
from pathlib import Path
import re

import yaml

from .core import DeckError, git, lock


def legacy_files(directory):
    return sorted(f for f in directory.glob("*.md") if re.fullmatch(r"T\d{3}[-_].+\.md", f.name))


def detect_source(project):
    for directory in (project.root / "docs/tasks", project.task_root):
        if directory.is_dir() and legacy_files(directory):
            return directory
    return None


def parse_task(file, project):
    if file.is_symlink() or not file.resolve().is_relative_to(project.root):
        raise DeckError(f"任务文档必须位于项目内：{file}")
    text = file.read_text(encoding="utf-8-sig")
    heading = re.search(r"^#\s+(T\d{3})\s+(.+?)\s*$", text, re.M)
    if not heading or heading[1] != file.name[:4]:
        raise DeckError(f"{file.name} 的任务 ID 与一级标题不一致")
    fields = {}
    for line in text.splitlines():
        match = re.fullmatch(r"\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|", line)
        if match:
            fields.setdefault(match[1].strip(), match[2].strip())
    if "依赖" not in fields:
        raise DeckError(f"{file.name} 缺少明确的依赖栏目")
    deps = list(dict.fromkeys(re.findall(r"(?<![A-Za-z0-9])T\d{3}(?!\d)", fields["依赖"])))
    if fields["依赖"] not in {"无", "无依赖", "—", "-", ""} and not deps:
        raise DeckError(f"{file.name} 的依赖栏目无法解析")
    acceptance = re.findall(r"^\s*-\s+\[[ xX]\]\s+(.+?)\s*$", text, re.M)
    relative = file.relative_to(project.root).as_posix()
    spec = {"id": heading[1], "title": heading[2], "depends_on": deps, "acceptance": acceptance,
            "source": relative, "source_status": fields.get("状态", "待开始"),
            "phase": fields.get("阶段", ""), "source_sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
    if spec["source_status"] == "已完成":
        candidate = git(project.root, "log", "-1", "--format=%H", "--", relative, check=False).stdout.strip()
        if candidate:
            spec["completion_candidate"] = candidate
    return spec


def import_markdown(project, source=None, dry_run=False):
    directory = Path(source) if source else detect_source(project)
    if directory is None:
        raise DeckError("未找到现有任务文档。可运行 taskdeck import docs/tasks，或点击「新建」。")
    if not directory.is_absolute():
        directory = project.root / directory
    directory = directory.resolve()
    if not directory.is_relative_to(project.root) or not directory.is_dir():
        raise DeckError("导入目录必须是当前项目内的文件夹")
    files = legacy_files(directory)
    if not files:
        raise DeckError("目录中没有 Txxx-*.md 格式的任务文档")
    with (contextlib.nullcontext() if dry_run else lock(project.runtime / "edit.lock")):
        existing = project.scan()
        incoming = {}
        for file in files:
            spec = parse_task(file, project)
            if spec["id"] in incoming:
                raise DeckError(f"源任务 ID 重复：{spec['id']}")
            incoming[spec["id"]] = (spec, file)
        graph = {ident: task.deps for ident, task in existing.items()}
        graph.update({ident: spec["depends_on"] for ident, (spec, _) in incoming.items() if ident not in existing})
        visiting, visited = set(), set()
        def visit(ident):
            if ident in visiting:
                raise DeckError(f"源任务存在循环依赖：{ident}")
            if ident in visited:
                return
            visiting.add(ident)
            for dep in graph[ident]:
                if dep not in graph:
                    raise DeckError(f"{ident} 的依赖不存在：{dep}")
                visit(dep)
            visiting.remove(ident)
            visited.add(ident)
        for ident in graph:
            visit(ident)
        planned = {ident: pair for ident, pair in incoming.items() if ident not in existing}
        for ident in planned:
            if (project.task_root / ident).exists():
                raise DeckError(f"目标文件夹已存在，拒绝覆盖：{ident}")
        if not dry_run:
            for ident, (spec, file) in planned.items():
                folder = project.task_root / ident
                folder.mkdir()
                (folder / "task.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False))
                (folder / "description.md").write_bytes(file.read_bytes())
        return {"source": str(directory), "found": len(files), "imported": len(planned),
                "skipped": len(incoming) - len(planned), "dry_run": dry_run,
                "reported_completed": [ident for ident, (spec, _) in incoming.items() if spec["source_status"] == "已完成"],
                "model_tasks_started": 0}
