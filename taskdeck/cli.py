from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from . import __version__
from .core import DeckError, Project, git
from .service import ensure_service, online, rpc, snapshot, view
from .importer import import_markdown


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parser():
    p = argparse.ArgumentParser(description="taskdeck · 手动启动的 Git 任务 / Codex 会话控制台")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-C", "--directory", default=".", help="项目内任意目录")
    sub = p.add_subparsers(dest="command")
    init = sub.add_parser("init", help="初始化当前 Git 项目；不创建或运行任务")
    init.add_argument("--tasks-dir", default="tasks")
    init.add_argument("--base", help="PR 目标分支；默认当前分支")
    add = sub.add_parser("add", help="创建任务文件夹")
    add.add_argument("id")
    add.add_argument("title")
    add.add_argument("--depends", default="", help="逗号分隔的前置任务 ID")
    ls = sub.add_parser("list", help="查看任务与依赖状态")
    ls.add_argument("--json", action="store_true")
    sub.add_parser("check", help="检查 ID、依赖、循环依赖和本机环境")
    imp = sub.add_parser("import", help="把现有 Txxx-*.md 导入任务文件夹，不启动任务")
    imp.add_argument("source", nargs="?", help="任务文档目录；默认识别 docs/tasks")
    imp.add_argument("--dry-run", action="store_true")
    for name, help_text in [("show", "任务详情"), ("edit", "用 EDITOR 编辑任务说明"),
                            ("start", "手动开始/继续任务"), ("chat", "进入同一个 Codex 对话"),
                            ("stop", "请求中断当前轮次"), ("diff", "查看分支和未提交修改")]:
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("id")
        if name == "start":
            cmd.add_argument("--prompt", help="本次启动的补充指令")
    send = sub.add_parser("send", help="运行中追加指令；空闲时开始下一轮")
    send.add_argument("id")
    send.add_argument("message")
    complete = sub.add_parser("complete", help="手动登记已入基线的交付提交")
    complete.add_argument("id")
    complete.add_argument("--commit", required=True)
    sub.add_parser("sync", help="刷新远端目标分支与 PR 状态；不启动任务")
    pr = sub.add_parser("pr", help="创建/复用该任务的 GitHub PR")
    pr.add_argument("id")
    pr.add_argument("--title")
    pr.add_argument("--body-file", type=Path)
    svc = sub.add_parser("service", help="后台服务状态/关闭")
    svc.add_argument("action", choices=["status", "stop"])
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "init":
            project = Project.init(args.directory, args.tasks_dir, args.base)
            print(f"已初始化：{project.root}\n任务目录：{project.task_root}\n目标分支：{project.base}\n运行 taskdeck 打开面板。")
            return 0
        project = Project.discover(args.directory)
        if args.command is None:
            from .tui import TaskDeck
            TaskDeck(project).run()
        elif args.command == "add":
            task = project.add(args.id, args.title, [x.strip() for x in args.depends.split(",") if x.strip()])
            print(f"已创建：{task.folder}\n编辑 task.yaml 和 description.md 后，手动开始。")
        elif args.command == "import":
            emit(import_markdown(project, args.source, args.dry_run))
        elif args.command == "list":
            data = snapshot(project)
            if args.json:
                emit(data)
            else:
                print(f"{project.root.name} / {project.base} · {len(data['tasks'])} 个任务")
                for row in data["tasks"]:
                    print(f"{row['id']:<12} {row['label']:<7} {row['title']}  依赖: {','.join(row['deps']) or '—'}")
        elif args.command == "check":
            tasks = project.scan()
            emit({"root": str(project.root), "tasks": len(tasks), "graph": "valid",
                  "codex": shutil.which(project.config.get("codex_command", ["codex"])[0]),
                  "base_exists": git(project.root, "rev-parse", "--verify", project.base_ref(), check=False).returncode == 0,
                  "service_running": online(project)})
        elif args.command == "show":
            task = project.task(args.id)
            emit({"definition": task.definition, "folder": str(task.folder), **view(project, args.id)})
        elif args.command == "edit":
            task = project.task(args.id)
            cmd = shlex.split(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi")
            subprocess.run([*cmd, str(task.folder / "description.md"), str(task.folder / "task.yaml")], check=True)
            project.scan()
        elif args.command == "chat":
            from .tui import Conversation, TaskDeck
            project.task(args.id)
            app = TaskDeck(project)
            app.call_after_refresh(lambda: app.push_screen(Conversation(project, args.id)))
            app.run()
        elif args.command == "diff":
            print(project.diff(args.id))
        elif args.command == "complete":
            ensure_service(project)
            emit(rpc(project, {"op": "complete", "task": args.id, "commit": args.commit}))
        elif args.command == "service":
            if args.action == "status":
                emit({"running": online(project), "socket": str(project.socket)})
            elif online(project):
                emit(rpc(project, {"op": "shutdown"}))
            else:
                print("服务未运行")
        elif args.command in {"start", "send", "stop", "sync", "pr"}:
            ensure_service(project)
            request = {"op": args.command}
            if hasattr(args, "id"):
                request["task"] = args.id
            if args.command == "start":
                request["text"] = args.prompt
            elif args.command == "send":
                request["text"] = args.message
            elif args.command == "pr":
                request.update(title=args.title, body=args.body_file.read_text() if args.body_file else None)
            emit(rpc(project, request))
        return 0
    except (DeckError, OSError, subprocess.CalledProcessError) as e:
        print(f"taskdeck: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
