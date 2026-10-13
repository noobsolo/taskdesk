from __future__ import annotations

import asyncio
import json
import webbrowser

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Grid, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Select, Static, TabbedContent, TabPane, TextArea

from .core import DeckError, LABELS, Project
from .service import ensure_service, rpc, snapshot
from .terminal import CodexPane, NativeTerminal
from .importer import detect_source, import_markdown, legacy_files


class CompletionModal(ModalScreen):
    CSS = """
    CompletionModal { align: center middle; }
    #completion-box { width: 80%; max-width: 100; height: auto; padding: 1 2; border: round #6caea3; background: #172026; }
    #completion-box Static { height: auto; margin: 1 0; }
    #completion-buttons { height: 3; }
    """
    BINDINGS = [("escape", "dismiss(None)", "返回")]

    def __init__(self, task):
        super().__init__()
        self.managed_task = task

    def compose(self):
        with Vertical(id="completion-box"):
            yield Label(f"确认 {self.managed_task.id} 已完成")
            yield Static("请确认这个提交包含本任务的真实交付。确认后会话仍可继续；代码进入目标基线后，才解除后续依赖。", markup=False)
            yield Input(value=self.managed_task.completion_candidate(), placeholder="实际交付/合并提交 SHA", id="completion-commit")
            with Horizontal(id="completion-buttons"):
                yield Button("确认完成", id="confirm-completion", variant="success")
                yield Button("返回", id="cancel-completion")

    @on(Button.Pressed)
    def respond(self, event):
        self.dismiss(self.query_one("#completion-commit", Input).value.strip() if event.button.id == "confirm-completion" else None)


class TaskDeck(App):
    TITLE = "taskdeck"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { background: #10171c; }
    Header { background: #1c2c32; }
    #summary { height: 2; padding: 0 1; color: #8bbfb6; }
    #filters { height: 3; }
    #search { width: 1fr; }
    #filter { width: 22; }
    #import { min-width: 12; }
    #main { height: 1fr; }
    #tasks { width: 45%; border: round #34454e; }
    #detail-pane { width: 55%; padding: 0 1; }
    #detail-title { height: 2; color: #8bbfb6; }
    TabbedContent { height: 1fr; }
    TabPane { padding: 0; }
    TextArea { border: none; background: #10171c; }
    #actions { height: 3; grid-size: 4 1; grid-columns: 1fr 1fr 1fr 1fr; grid-rows: 3; grid-gutter: 0 1; }
    #actions Button { min-width: 10; width: 1fr; margin: 0; }
    #notice { height: 1; color: #ddb875; padding: 0 1; }
    """
    BINDINGS = [("ctrl+q", "quit", "退出面板"), ("s", "start", "开始"),
                ("slash", "search", "搜索"), ("r", "sync", "同步 PR"), ("c", "complete", "确认完成"),
                ("ctrl+g", "task_list", "任务列表")]

    def __init__(self, project):
        super().__init__()
        self.project = project
        self.rows = []
        self.selected = None
        self.signature = None
        self.refreshing = False
        self.rendering = False
        self.import_source = detect_source(project)

    def compose(self):
        yield Header()
        yield Static("", id="summary", markup=False)
        with Horizontal(id="filters"):
            yield Input(placeholder="搜索 ID / 标题 / 依赖", id="search")
            yield Select([("全部任务", "all"), ("可开始", "ready"), ("运行中", "running"),
                          ("等你处理", "waiting"), ("待核对", "unverified"), ("待入基线", "delivered"), ("被阻塞", "blocked"), ("有 PR", "pr")], value="all", allow_blank=False, id="filter")
            yield Button("导入任务", id="import")
        with Horizontal(id="main"):
            yield DataTable(id="tasks", cursor_type="row", zebra_stripes=True)
            with Vertical(id="detail-pane"):
                yield Static("选择任务", id="detail-title", markup=False)
                with TabbedContent(initial="spec-tab"):
                    with TabPane("Codex", id="codex-tab"):
                        yield CodexPane(self.project, id="codex-pane")
                    with TabPane("任务", id="spec-tab"):
                        yield TextArea("", read_only=True, id="spec")
                    with TabPane("依赖", id="deps-tab"):
                        yield TextArea("", read_only=True, id="deps")
                    with TabPane("记录", id="events-tab"):
                        yield TextArea("", read_only=True, id="events")
        yield Static("导入任务或运行 taskdeck add 登记任务，然后手动开始。", id="notice", markup=False)
        with Grid(id="actions"):
            yield Button("开始", id="start", variant="success")
            yield Button("确认完成", id="complete", disabled=True)
            yield Button("打开 PR", id="open-pr")
            yield Button("同步", id="sync")
        yield Footer()

    async def on_mount(self):
        tabs = self.query_one(TabbedContent)
        tabs.active = "spec-tab"
        tabs.hide_tab("codex-tab")
        table = self.query_one("#tasks", DataTable)
        table.add_columns("ID", "任务", "状态", "PR")
        await self.reload()
        self.set_interval(1.5, self.reload)
        table.focus()

    async def reload(self):
        if self.refreshing or self.screen is not self.screen_stack[0]:
            return
        self.refreshing = True
        try:
            data = await asyncio.to_thread(snapshot, self.project)
            self.rows = data["tasks"]
            counts = {s: sum(r["status"] == s for r in self.rows) for s in ("ready", "running", "waiting", "done")}
            counts["running"] = sum(r.get("execution_status") in {"running", "preparing"} for r in self.rows)
            counts["waiting"] = sum(r.get("execution_status") == "waiting" for r in self.rows)
            summary = f"{self.project.root.name}  /  {self.project.base}     {len(self.rows)} 个任务 · 可开始 {counts['ready']} · 运行 {counts['running']} · 等你 {counts['waiting']} · 完成 {counts['done']}"
            self.query_one("#summary", Static).update(summary)
            self.draw_table()
        except DeckError as e:
            self.query_one("#notice", Static).update(str(e))
        finally:
            self.refreshing = False

    def draw_table(self):
        query = self.query_one("#search", Input).value.casefold()
        selected_filter = self.query_one("#filter", Select).value
        rows = [r for r in self.rows if query in (r["id"] + " " + r["title"] + " " + " ".join(r["deps"])).casefold()]
        if selected_filter == "running":
            rows = [r for r in rows if r.get("execution_status", r["status"]) in {"running", "waiting", "preparing"}]
        elif selected_filter == "waiting":
            rows = [r for r in rows if r.get("execution_status", r["status"]) == "waiting"]
        elif selected_filter == "pr":
            rows = [r for r in rows if r["state"].get("pr")]
        elif selected_filter != "all":
            rows = [r for r in rows if r["status"] == selected_filter]
        signature = [(r["id"], r["title"], r["status"], str(r["state"].get("pr"))) for r in rows]
        table = self.query_one("#tasks", DataTable)
        if signature != self.signature:
            self.rendering = True
            table.clear()
            for r in rows:
                color = {"running": "cyan", "waiting": "yellow", "done": "green", "failed": "red"}.get(r["status"], "white")
                pr = r["state"].get("pr")
                table.add_row(r["id"], r["title"], Text(r["label"], style=color), f"#{pr['number']} {pr['state']}" if pr else "—", key=r["id"])
            ids = [r["id"] for r in rows]
            self.selected = self.selected if self.selected in ids else (ids[0] if ids else None)
            if self.selected:
                table.move_cursor(row=ids.index(self.selected))
            self.signature = signature
            self.rendering = False
        self.details()

    @on(Input.Changed, "#search")
    @on(Select.Changed, "#filter")
    def filter_changed(self):
        if self.is_mounted:
            self.draw_table()

    @on(DataTable.RowHighlighted, "#tasks")
    def selected_row(self, event):
        if not self.rendering:
            self.selected = str(event.row_key.value)
            self.details()

    def details(self):
        row = next((r for r in self.rows if r["id"] == self.selected), None)
        tabs = self.query_one(TabbedContent)
        has_session = bool(row and row["state"].get("thread_id"))
        if has_session:
            tabs.show_tab("codex-tab")
        else:
            if tabs.active == "codex-tab":
                tabs.active = "spec-tab"
            tabs.hide_tab("codex-tab")
        pane = self.query_one("#codex-pane", CodexPane)
        if tabs.active == "codex-tab" and self.selected != pane.ident:
            self.connect_session(self.selected)
        if not row:
            self.query_one("#detail-title", Static).update("暂无任务")
            hint = "点击「导入任务」导入已有任务，或运行 taskdeck add 登记任务。"
            if not self.rows and self.import_source:
                count = len(legacy_files(self.import_source))
                hint = f"发现 {self.import_source.relative_to(self.project.root)} 中有 {count} 份任务文档，点击「导入任务」。导入不会启动 Codex。"
            elif self.rows:
                hint = "没有匹配的任务，请清空搜索或切换筛选。"
            for ident in ("spec", "deps", "events"):
                self.query_one("#" + ident, TextArea).load_text(hint if ident == "spec" else "")
            self.query_one("#notice", Static).update(hint)
            for ident in ("start", "open-pr", "complete"):
                self.query_one("#" + ident, Button).disabled = True
            return
        self.query_one("#detail-title", Static).update(f"{row['id']}  {row['title']}  ·  {row['label']}")
        task = self.project.task(row["id"])
        state = row["state"]
        text = task.description() + "\n\n验收要求\n" + "\n".join("□ " + x for x in task.definition.get("acceptance", []))
        text += f"\n\n任务目录：{task.folder}\n分支：{state.get('branch', '—')}\nSession：{state.get('thread_id', '—')}"
        if state.get("thread_id"):
            text += "\n会话状态：" + LABELS.get(row.get("execution_status"), row.get("execution_status", "—"))
        if state.get("delivery_commit"):
            text += f"\n已确认交付：{state['delivery_commit']}"
        if task.definition.get("source"):
            text += f"\n原始任务：{task.definition['source']}\n原文状态：{task.definition.get('source_status', '—')}"
        if row["status"] == "unverified":
            text += "\n\n原文档标记已完成。请点击「确认完成」核对实际交付提交；尚未纳入目标基线的任务会继续阻塞后续。"
        if state.get("plan"):
            text += "\n\nCodex 当前计划\n" + "\n".join(f"{x.get('status', '')} · {x.get('step', '')}" for x in state["plan"])
        if state.get("error"):
            text += "\n\n运行错误：" + str(state["error"])
        self.set_text("spec", text)
        by_id = {r["id"]: r for r in self.rows}
        lines = ["前置依赖（展开完整上游链）"]
        expanded = {row["id"]}
        def walk(ident, prefix=""):
            for dep in by_id[ident]["deps"]:
                entry = by_id[dep]
                lines.append(prefix + ("✓ " if entry["included"] else "· ") + f"{dep} {entry['title']} [{entry['label']}]")
                if dep in expanded:
                    lines[-1] += "（已在上方展开）"
                else:
                    expanded.add(dep)
                    walk(dep, prefix + "  ")
        walk(row["id"])
        if not row["deps"]:
            lines.append("无前置依赖")
        lines += ["", "阻塞原因", *(row["blockers"] or ["依赖已满足"]), "", f"直接后续任务（{row['unlocks']} 个）"]
        lines += [f"{r['id']} {r['title']} [{r['label']}]" for r in self.rows if row["id"] in r["deps"]]
        self.set_text("deps", "\n".join(lines))
        event_file = task.local / "events.jsonl"
        self.set_text("events", event_file.read_text()[-40000:] if event_file.exists() else "尚无运行记录")
        self.query_one("#open-pr", Button).disabled = not bool(state.get("pr"))
        self.query_one("#start", Button).disabled = row["status"] in {"unverified", "done", "delivered"} or bool(row["blockers"])
        self.query_one("#complete", Button).disabled = bool(row.get("delivery_confirmed", row["status"] == "done"))
        self.query_one("#notice", Static).update("选择任务后手动开始；导入、浏览、确认完成都不会启动 Codex 任务。")

    def set_text(self, ident, text):
        area = self.query_one("#" + ident, TextArea)
        if area.text != text:
            position = area.scroll_y
            area.load_text(text)
            area.scroll_to(y=position, animate=False)

    def action_search(self):
        self.query_one("#search", Input).focus()

    def check_action(self, action, parameters):
        if isinstance(self.focused, NativeTerminal) and action not in {"quit", "task_list"}:
            return False
        return True

    def action_task_list(self):
        self.query_one("#tasks", DataTable).focus()

    def open_session_tab(self):
        if self.selected and self.project.task(self.selected).state.get("thread_id"):
            self.query_one(TabbedContent).active = "codex-tab"
            self.connect_session(self.selected)

    @on(TabbedContent.TabActivated)
    def session_tab_activated(self, event):
        if (event.pane.id == "codex-tab" and event.tabbed_content.active == "codex-tab"
                and self.selected and self.project.task(self.selected).state.get("thread_id")):
            self.connect_session(self.selected)

    @work(group="native-attach")
    async def connect_session(self, ident):
        await self.query_one("#codex-pane", CodexPane).select_task(ident)

    @work(group="task-action")
    async def action_start(self):
        ident = self.selected
        if not ident:
            self.notify("请先导入任务或运行 taskdeck add 登记任务，再选择要开始的任务", severity="warning")
            return
        try:
            self.notify(f"准备 {ident} 的工作目录和会话…")
            await asyncio.to_thread(ensure_service, self.project)
            await asyncio.to_thread(rpc, self.project, {"op": "start", "task": ident})
            await self.reload()
            if self.selected == ident:
                self.open_session_tab()
        except DeckError as e:
            self.notify(str(e), severity="error", timeout=10)
            await self.reload()

    @work(exclusive=True, group="sync")
    async def action_sync(self):
        try:
            await asyncio.to_thread(ensure_service, self.project)
            result = await asyncio.to_thread(rpc, self.project, {"op": "sync"})
            self.notify("；".join(result["warnings"]) or "基线与 PR 已同步", timeout=8)
            await self.reload()
        except DeckError as e:
            self.notify(str(e), severity="error")

    @work(exclusive=True, group="import")
    async def import_tasks(self):
        try:
            result = await asyncio.to_thread(import_markdown, self.project)
            self.notify(f"导入 {result['imported']} 个任务，保留 {result['skipped']} 个已有任务；没有启动 Codex", timeout=8)
            await self.reload()
        except DeckError as e:
            self.notify(str(e), severity="error", timeout=10)

    def confirm_completion(self):
        row = next((r for r in self.rows if r["id"] == self.selected), None)
        if row and not row.get("delivery_confirmed", row["status"] == "done"):
            ident = self.selected
            self.push_screen(CompletionModal(self.project.task(ident)), lambda commit: self.record_completion(ident, commit))
        else:
            self.notify("请先选择一个尚未确认完成的任务", severity="warning")

    def action_complete(self):
        self.confirm_completion()

    @work(group="completion")
    async def record_completion(self, ident, commit):
        if not commit:
            return
        try:
            # State writes use Task.save's file lock; no daemon restart, new
            # model turn, or turn interruption is needed to confirm delivery.
            await asyncio.to_thread(self.project.record_completion, ident, commit)
            task = self.project.task(ident)
            included = self.project.has_commit(task.state["delivery_commit"])
            self.notify("已确认完成，后续任务可手动开始" if included else "已确认开发完成，等待合入目标分支；会话可以继续", timeout=8)
            await self.reload()
        except DeckError as e:
            self.notify(str(e), severity="error", timeout=10)

    @on(Button.Pressed)
    def buttons(self, event):
        mapping = {"start": self.action_start, "sync": self.action_sync,
                   "import": self.import_tasks, "complete": self.confirm_completion}
        if event.button.id in mapping:
            mapping[event.button.id]()
        elif event.button.id == "open-pr" and self.selected:
            pr = self.project.task(self.selected).state.get("pr")
            if pr:
                webbrowser.open(pr["url"])
