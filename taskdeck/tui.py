from __future__ import annotations

import asyncio
import json
from pathlib import Path
import webbrowser

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Select, Static, TabbedContent, TabPane, TextArea

from .core import DeckError, LABELS, Project, read_json
from .service import ensure_service, rpc, snapshot, view


def transcript_text(entries):
    labels = {"userMessage": "你", "agentMessage": "Codex", "commandExecution": "命令", "fileChange": "文件修改"}
    return "\n\n".join(f"{labels.get(e['kind'], e['kind'])}  {e.get('time', '')[11:19]}\n{e['text']}" for e in entries)


class RequestModal(ModalScreen):
    CSS = """
    RequestModal { align: center middle; }
    #request-box { width: 85%; max-width: 110; height: 85%; padding: 1 2; border: round #ddb875; background: #172026; }
    #request-data { height: 1fr; }
    #question-fields { height: auto; max-height: 60%; }
    #request-actions { height: 3; }
    #request-actions Button { margin-right: 1; }
    """
    BINDINGS = [("escape", "dismiss(None)", "返回")]

    def __init__(self, request):
        super().__init__()
        self.request = request

    def compose(self):
        params = self.request["params"]
        with Vertical(id="request-box"):
            yield Label("Codex 等待你的处理")
            yield TextArea(json.dumps(params, ensure_ascii=False, indent=2), read_only=True, id="request-data")
            with VerticalScroll(id="question-fields"):
                for index, q in enumerate(params.get("questions", [])):
                    yield Label(q.get("question", q.get("header", "问题")))
                    options = q.get("options") or []
                    if options:
                        yield Label("  /  ".join(o.get("label", "") for o in options))
                    yield Input(placeholder="输入回答或选项文字", id=f"answer-{index}")
                if self.request["method"] == "mcpServer/elicitation/request":
                    yield Input(placeholder='表单回答 JSON，例如 {"value":"..."}', id="mcp-answer")
            with Horizontal(id="request-actions"):
                yield Button("提交回答" if params.get("questions") else "允许本次", id="allow", variant="success")
                yield Button("拒绝", id="deny", variant="error")
                yield Button("返回", id="cancel")

    @on(Button.Pressed)
    def respond(self, event):
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "deny":
            if self.request["method"] == "item/tool/requestUserInput":
                self.dismiss(None)
            else:
                self.dismiss("decline")
        elif event.button.id == "allow":
            if self.request["params"].get("questions"):
                answers = {q["id"]: self.query_one(f"#answer-{i}", Input).value
                           for i, q in enumerate(self.request["params"]["questions"])}
                if not all(answers.values()):
                    self.notify("请回答所有问题", severity="warning")
                    return
                self.dismiss(answers)
            elif self.request["method"] == "mcpServer/elicitation/request":
                try:
                    content = json.loads(self.query_one("#mcp-answer", Input).value or "{}")
                except ValueError:
                    self.notify("请输入合法 JSON", severity="error")
                    return
                self.dismiss({"action": "accept", "content": content})
            else:
                self.dismiss("accept")


class NewTaskModal(ModalScreen):
    CSS = """
    NewTaskModal { align: center middle; }
    #new-box { width: 75%; max-width: 85; height: auto; padding: 1 2; border: round #6caea3; background: #172026; }
    #new-box Input { margin: 1 0; }
    #new-buttons { height: 3; }
    """
    BINDINGS = [("escape", "dismiss(None)", "返回")]

    def compose(self):
        with Vertical(id="new-box"):
            yield Label("新建任务 · 创建后等待你手动开始")
            yield Input(placeholder="任务 ID，例如 T001", id="new-id")
            yield Input(placeholder="任务标题", id="new-title")
            yield Input(placeholder="前置依赖，逗号分隔，例如 T001,T002", id="new-deps")
            with Horizontal(id="new-buttons"):
                yield Button("创建", id="create", variant="success")
                yield Button("返回", id="cancel-new")

    @on(Button.Pressed)
    def respond(self, event):
        if event.button.id == "cancel-new":
            self.dismiss(None)
        else:
            self.dismiss({"id": self.query_one("#new-id", Input).value.strip(),
                          "title": self.query_one("#new-title", Input).value.strip(),
                          "deps": [x.strip() for x in self.query_one("#new-deps", Input).value.split(",") if x.strip()]})


class Conversation(Screen):
    CSS = """
    #conversation-info { height: auto; max-height: 4; padding: 0 1; color: #8bbfb6; }
    #transcript { height: 1fr; border: round #34454e; }
    #composer { height: 6; border: round #6caea3; }
    #chat-buttons { height: 3; }
    #chat-buttons Button { margin-right: 1; }
    """
    BINDINGS = [("escape", "back", "任务面板"), ("ctrl+s", "send", "发送/调整"),
                ("ctrl+x", "stop", "中断当前轮次")]

    def __init__(self, project, ident):
        super().__init__()
        self.project, self.ident = project, ident
        self.requests = []
        self.refreshing = False

    def compose(self):
        yield Header()
        yield Static(f"{self.ident} · 正在连接", id="conversation-info", markup=False)
        yield TextArea("尚无对话。请在任务面板手动开始。", read_only=True, id="transcript")
        yield TextArea(id="composer")
        with Horizontal(id="chat-buttons"):
            yield Button("发送 / 调整", id="send", variant="primary")
            yield Button("中断轮次", id="stop", variant="warning")
            yield Button("处理请求", id="request")
            yield Button("返回面板", id="back")
        yield Footer()

    async def on_mount(self):
        await self.refresh_view()
        self.set_interval(0.7, self.refresh_view)
        self.query_one("#composer", TextArea).focus()

    async def refresh_view(self):
        if self.refreshing:
            return
        self.refreshing = True
        try:
            data = await asyncio.to_thread(view, self.project, self.ident)
            state = data["state"]
            self.requests = data["requests"]
            info = f"{self.ident} · {LABELS.get(state.get('status'), state.get('status', '未开始'))} · session {state.get('thread_id', '—')}"
            if state.get("error"):
                info += "\n" + str(state["error"])
            self.query_one("#conversation-info", Static).update(info)
            text = transcript_text(data["transcript"]) or "尚无对话。请在任务面板手动开始。"
            transcript = self.query_one("#transcript", TextArea)
            if transcript.text != text:
                follow = transcript.scroll_y >= transcript.max_scroll_y - 2
                position = transcript.scroll_y
                transcript.load_text(text)
                if follow:
                    transcript.scroll_end(animate=False)
                else:
                    transcript.scroll_to(y=position, animate=False)
            self.query_one("#request", Button).disabled = not self.requests
            self.query_one("#stop", Button).disabled = not state.get("turn_id")
        except DeckError as e:
            self.query_one("#conversation-info", Static).update(str(e))
        finally:
            self.refreshing = False

    @work(exclusive=True, group="chat-action")
    async def action_send(self):
        composer = self.query_one("#composer", TextArea)
        text = composer.text.strip()
        if not text:
            return
        try:
            await asyncio.to_thread(ensure_service, self.project)
            await asyncio.to_thread(rpc, self.project, {"op": "send", "task": self.ident, "text": text})
            composer.clear()
            await self.refresh_view()
            self.notify("消息已交给当前会话")
        except DeckError as e:
            self.notify(str(e), severity="error", timeout=8)

    @work(exclusive=True, group="chat-stop")
    async def action_stop(self):
        try:
            await asyncio.to_thread(rpc, self.project, {"op": "stop", "task": self.ident})
            self.notify("已请求中断，等待 Codex 完成中断")
        except DeckError as e:
            self.notify(str(e), severity="error")

    def action_back(self):
        self.app.pop_screen()

    @on(Button.Pressed)
    def buttons(self, event):
        event.stop()
        if event.button.id == "send":
            self.action_send()
        elif event.button.id == "stop":
            self.action_stop()
        elif event.button.id == "back":
            self.action_back()
        elif event.button.id == "request" and self.requests:
            request = self.requests[0]
            self.app.push_screen(RequestModal(request), lambda result: self.answer(request, result))

    @work(group="answer")
    async def answer(self, request, result):
        if result is None:
            return
        try:
            await asyncio.to_thread(rpc, self.project, {"op": "answer", "task": self.ident,
                                                       "request_id": request["id"], "response": result})
            await self.refresh_view()
        except DeckError as e:
            self.notify(str(e), severity="error")


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
    #main { height: 1fr; }
    #tasks { width: 45%; border: round #34454e; }
    #detail-pane { width: 55%; padding: 0 1; }
    #detail-title { height: 2; color: #8bbfb6; }
    TabbedContent { height: 1fr; }
    TabPane { padding: 0; }
    TextArea { border: none; background: #10171c; }
    #actions { height: 3; }
    #actions Button { min-width: 10; margin-right: 1; }
    #notice { height: 1; color: #ddb875; padding: 0 1; }
    """
    BINDINGS = [("ctrl+q", "quit", "退出面板"), ("s", "start", "开始"),
                ("enter", "conversation", "进入会话"), ("slash", "search", "搜索"),
                ("r", "sync", "同步 PR"), ("n", "new", "新建")]

    def __init__(self, project):
        super().__init__()
        self.project = project
        self.rows = []
        self.selected = None
        self.signature = None
        self.refreshing = False
        self.rendering = False

    def compose(self):
        yield Header()
        yield Static("", id="summary", markup=False)
        with Horizontal(id="filters"):
            yield Input(placeholder="搜索 ID / 标题 / 依赖", id="search")
            yield Select([("全部任务", "all"), ("可开始", "ready"), ("运行中", "running"),
                          ("等你处理", "waiting"), ("被阻塞", "blocked"), ("有 PR", "pr")], value="all", allow_blank=False, id="filter")
        with Horizontal(id="main"):
            yield DataTable(id="tasks", cursor_type="row", zebra_stripes=True)
            with Vertical(id="detail-pane"):
                yield Static("选择任务", id="detail-title", markup=False)
                with TabbedContent():
                    with TabPane("任务", id="spec-tab"):
                        yield TextArea("", read_only=True, id="spec")
                    with TabPane("依赖", id="deps-tab"):
                        yield TextArea("", read_only=True, id="deps")
                    with TabPane("改动", id="diff-tab"):
                        yield TextArea("点击「查看改动」加载", read_only=True, id="diff")
                    with TabPane("记录", id="events-tab"):
                        yield TextArea("", read_only=True, id="events")
        yield Static("新建任务后编辑 description.md 与 task.yaml，再手动开始。", id="notice", markup=False)
        with Horizontal(id="actions"):
            yield Button("新建", id="new")
            yield Button("开始", id="start", variant="success")
            yield Button("进入会话", id="enter", variant="primary")
            yield Button("查看改动", id="show-diff")
            yield Button("创建 PR", id="create-pr")
            yield Button("打开 PR", id="open-pr")
            yield Button("同步", id="sync")
        yield Footer()

    async def on_mount(self):
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
            rows = [r for r in rows if r["status"] in {"running", "waiting", "preparing"}]
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

    @on(DataTable.RowSelected, "#tasks")
    def enter_row(self, event):
        self.selected = str(event.row_key.value)
        self.action_conversation()

    def details(self):
        row = next((r for r in self.rows if r["id"] == self.selected), None)
        if not row:
            self.query_one("#detail-title", Static).update("暂无任务 · 点击新建或运行 taskdeck add")
            for ident in ("spec", "deps", "events"):
                self.query_one("#" + ident, TextArea).load_text("")
            return
        self.query_one("#detail-title", Static).update(f"{row['id']}  {row['title']}  ·  {row['label']}")
        task = self.project.task(row["id"])
        state = row["state"]
        text = task.description() + "\n\n验收要求\n" + "\n".join("□ " + x for x in task.definition.get("acceptance", []))
        text += f"\n\n任务目录：{task.folder}\n分支：{state.get('branch', '—')}\nSession：{state.get('thread_id', '—')}"
        if state.get("plan"):
            text += "\n\nCodex 当前计划\n" + "\n".join(f"{x.get('status', '')} · {x.get('step', '')}" for x in state["plan"])
        if state.get("error"):
            text += "\n\n运行错误：" + str(state["error"])
        self.set_text("spec", text)
        by_id = {r["id"]: r for r in self.rows}
        lines = ["前置依赖（展开完整上游链）"]
        def walk(ident, prefix="", seen=None):
            seen = seen or set()
            for dep in by_id[ident]["deps"]:
                entry = by_id[dep]
                lines.append(prefix + ("✓ " if entry["included"] else "· ") + f"{dep} {entry['title']} [{entry['label']}]")
                if dep not in seen:
                    walk(dep, prefix + "  ", seen | {dep})
        walk(row["id"])
        if not row["deps"]:
            lines.append("无前置依赖")
        lines += ["", "阻塞原因", *(row["blockers"] or ["依赖已满足"]), "", f"直接后续任务（{row['unlocks']} 个）"]
        lines += [f"{r['id']} {r['title']} [{r['label']}]" for r in self.rows if row["id"] in r["deps"]]
        self.set_text("deps", "\n".join(lines))
        event_file = task.local / "events.jsonl"
        self.set_text("events", event_file.read_text()[-40000:] if event_file.exists() else "尚无运行记录")
        self.query_one("#open-pr", Button).disabled = not bool(state.get("pr"))

    def set_text(self, ident, text):
        area = self.query_one("#" + ident, TextArea)
        if area.text != text:
            position = area.scroll_y
            area.load_text(text)
            area.scroll_to(y=position, animate=False)

    def action_search(self):
        self.query_one("#search", Input).focus()

    def action_conversation(self):
        if self.selected:
            self.push_screen(Conversation(self.project, self.selected))

    def action_new(self):
        self.push_screen(NewTaskModal(), self.create_task)

    @work(group="create")
    async def create_task(self, values):
        if values is None:
            return
        try:
            await asyncio.to_thread(self.project.add, values["id"], values["title"], values["deps"])
            self.selected = values["id"]
            self.notify("任务已创建，请编辑任务说明后手动开始")
            await self.reload()
        except DeckError as e:
            self.notify(str(e), severity="error")

    @work(group="task-action")
    async def action_start(self):
        ident = self.selected
        if not ident:
            return
        try:
            self.notify(f"准备 {ident} 的工作目录和会话…")
            await asyncio.to_thread(ensure_service, self.project)
            await asyncio.to_thread(rpc, self.project, {"op": "start", "task": ident})
            await self.reload()
            self.push_screen(Conversation(self.project, ident))
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

    @work(group="diff")
    async def show_diff(self):
        ident = self.selected
        if ident:
            try:
                text = await asyncio.to_thread(self.project.diff, ident)
                self.set_text("diff", text)
                self.query_one(TabbedContent).active = "diff-tab"
            except DeckError as e:
                self.notify(str(e), severity="error")

    @work(group="pr")
    async def create_pr(self):
        if not self.selected:
            return
        try:
            await asyncio.to_thread(ensure_service, self.project)
            result = await asyncio.to_thread(rpc, self.project, {"op": "pr", "task": self.selected})
            self.notify(result["url"], timeout=10)
            await self.reload()
        except DeckError as e:
            self.notify(str(e), severity="error", timeout=10)

    @on(Button.Pressed)
    def buttons(self, event):
        mapping = {"new": self.action_new, "start": self.action_start, "enter": self.action_conversation,
                   "show-diff": self.show_diff, "create-pr": self.create_pr, "sync": self.action_sync}
        if event.button.id in mapping:
            mapping[event.button.id]()
        elif event.button.id == "open-pr" and self.selected:
            pr = self.project.task(self.selected).state.get("pr")
            if pr:
                webbrowser.open(pr["url"])
