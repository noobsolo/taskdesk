"""Capture/profile real Codex CLI in the actual TaskDeck layout, without a model turn.

Run on a host that permits local sockets:
  .venv/bin/python docs/check_terminal_scroll.py /private/tmp/taskdeck-scroll-baseline

Private raw PTY output, snapshots, screenshots and metrics stay in OUTPUT.
"""
import asyncio
import base64
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

os.environ.pop("NO_COLOR", None)

from rich.cells import cell_len
from rich.console import Console
from rich.segment import Segment
from textual import events
from textual.widgets import TabbedContent

from taskdeck.codex import Codex
from taskdeck.core import Project, git
import taskdeck.terminal as native
if "--baseline" in sys.argv[2:]:
    # Pin the pre-optimization version so committing this change doesn't turn
    # the comparison into two runs of the updated renderer.
    baseline_ref = "a9def48eff857ff24081c6ff55063737b19622b1"
    source = subprocess.run(["git", "show", baseline_ref + ":taskdeck/terminal.py"],
                            cwd=Path(__file__).resolve().parent.parent,
                            capture_output=True, check=True).stdout
    exec(compile(source, str(native.__file__), "exec"), native.__dict__)
from taskdeck.tui import TaskDeck


def normalize_svg(path):
    tree = ET.parse(path)
    for element in list(tree.getroot().iter()):
        value, length = element.text or "", element.get("textLength")
        if not value or not length:
            continue
        width, offset = float(length) / len(value), float(element.get("x", "0"))
        element.attrib.pop("textLength", None)
        element.text = None
        for char in value:
            if char in "\r\n":
                continue
            cells = cell_len(char)
            span = ET.SubElement(element, "{http://www.w3.org/2000/svg}tspan", {"x": str(offset)})
            if cells:
                span.set("textLength", str(width * cells))
                span.set("lengthAdjust", "spacingAndGlyphs")
            span.text = char
            offset += width * cells
    path.write_text(ET.tostring(tree.getroot(), encoding="unicode"))


async def main(output):
    output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="taskdeck-live-scroll-", dir="/private/tmp"))
    git(root, "init", "-b", "main")
    git(root, "-c", "user.name=Terminal Check", "-c", "user.email=check@example.invalid",
        "commit", "--allow-empty", "-m", "isolated terminal check")
    project = Project.init(root)
    log = (output / "codex.log").open("ab")
    notifications = []
    async def notified(message):
        notifications.append(message["method"])
    codex = Codex(["codex", "app-server"], root, notified, log)
    codex.endpoint = "unix://" + str(root / "codex.sock")
    trace, renders, wheels, snapshots = [], [], [], []
    pending_wheel = None
    thread = None
    read = os.read
    resize = native.PtySession.resize
    render = native.NativeTerminal.render
    scroll_up = native.NativeTerminal.on_mouse_scroll_up
    masters = set()
    def traced_read(fd, size):
        data = read(fd, size)
        if fd in masters and data:
            trace.append({"kind": "output", "data": base64.b64encode(data).decode()})
        return data
    def traced_resize(session, columns, lines):
        masters.add(session.master)
        trace.append({"kind": "resize", "columns": columns, "lines": lines})
        return resize(session, columns, lines)
    def timed_render(terminal):
        start = time.perf_counter()
        result = render(terminal)
        renders.append((time.perf_counter() - start) * 1000)
        return result
    def timed_scroll(terminal, event):
        nonlocal pending_wheel
        pending_wheel = time.perf_counter()
        return scroll_up(terminal, event)
    try:
        await codex.connect()
        thread = (await codex.rpc("thread/start", {"cwd": str(root), "sandbox": "read-only"}))["thread"]["id"]
        await codex.rpc("thread/name/set", {"threadId": thread, "name": "Taskdeck terminal scrolling check"})
        project.add("T001", "真实 CLI 滚动检查").save(status="idle", thread_id=thread)
        command = ["codex", "resume", "--remote", codex.endpoint, "--no-alt-screen", "-C", str(root), thread]
        native.ensure_service = lambda project: None
        native.rpc = lambda project, request: {"command": command, "cwd": str(root)}
        os.read = traced_read
        native.PtySession.resize = traced_resize
        native.NativeTerminal.render = timed_render
        native.NativeTerminal.on_mouse_scroll_up = timed_scroll
        app = TaskDeck(project)
        async with app.run_test(size=(180, 54)) as pilot:
            compositor = app.screen._compositor
            update = compositor.render_update
            def timed_update(*args, **kwargs):
                nonlocal pending_wheel
                result = update(*args, **kwargs)
                if pending_wheel is not None:
                    wheels.append((time.perf_counter() - pending_wheel) * 1000)
                    pending_wheel = None
                return result
            compositor.render_update = timed_update
            await pilot.pause()
            await pilot.click(app.query_one(TabbedContent).get_tab("codex-tab"))
            terminal = None
            for _ in range(150):
                found = list(app.query(native.NativeTerminal))
                if found and found[0].session:
                    terminal = found[0]
                    text = native.screen_text(terminal.session.screen).plain
                    if "Trust this folder?" in text:
                        terminal.session.write(b"\r")
                    if "Ask Codex to do anything" in text and "Resuming session" not in text:
                        break
                await asyncio.sleep(0.1)
            assert terminal and terminal.session and not terminal.session.closed and "Resuming session" not in text and "Error:" not in text, "Codex CLI didn't connect"
            session = terminal.session
            def snapshot(label):
                screen = session.screen
                console = Console(width=screen.columns, force_terminal=True, color_system="truecolor")
                rows = console.render_lines(native.screen_text(screen), console.options, pad=False)
                snapshots.append({"label": label, "trace_length": len(trace), "columns": screen.columns,
                                  "lines": screen.lines, "rows": [[(s.text, str(s.style))
                                  for s in Segment.simplify(row)] for row in rows]})
                filename = label + ".svg"
                app.save_screenshot(filename, path=str(output))
                normalize_svg(output / filename)
            await asyncio.sleep(0.4)
            terminal.focus()
            snapshot("connected")
            for _ in range(12):
                await pilot.press("ctrl+u", *list("/status"), "enter")
                await asyncio.sleep(0.25)
            await asyncio.sleep(0.4)
            snapshot("status-history")
            await pilot.press("ctrl+u", *list("测试中文输入"))
            await asyncio.sleep(0.4)
            assert "测试中文输入" in native.screen_text(session.screen).plain
            snapshot("chinese-input")
            await pilot.press("ctrl+u", "slash")
            await asyncio.sleep(0.4)
            snapshot("slash-menu")
            await pilot.press("escape", "ctrl+u")
            await asyncio.sleep(0.3)
            await pilot.resize_terminal(140, 42)
            await asyncio.sleep(0.4)
            snapshot("resized")
            await pilot.resize_terminal(180, 54)
            await asyncio.sleep(0.4)
            for _ in range(20):
                terminal.post_message(events.MouseScrollUp(terminal, 2, 2, 0, -1, 0, False, False, False))
                await asyncio.sleep(0.08)
            snapshot("scroll-up")
            page = await codex.rpc("thread/turns/list", {"threadId": thread, "limit": 100})
            assert not page.get("data"), "Unexpected model turn in a local-command check"
            metrics = {"viewport": [session.screen.columns, session.screen.lines], "raw_bytes": sum(len(base64.b64decode(e["data"])) for e in trace if e["kind"] == "output"),
                       "render_count": len(renders), "render_median_ms": statistics.median(renders),
                       "wheel_samples": len(wheels), "wheel_to_composition_median_ms": statistics.median(wheels) if wheels else None,
                       "model_turns": 0, "root": str(root)}
            (output / "trace.json").write_text(json.dumps({"events": trace, "snapshots": snapshots}))
            (output / "metrics.json").write_text(json.dumps(metrics, indent=2))
            print(json.dumps(metrics), flush=True)
    finally:
        (output / "trace.json").write_text(json.dumps({"events": trace, "snapshots": snapshots}))
        os.read = read
        native.PtySession.resize = resize
        native.NativeTerminal.render = render
        native.NativeTerminal.on_mouse_scroll_up = scroll_up
        if thread:
            await codex.rpc("thread/archive", {"threadId": thread})
        await codex.close()
        log.close()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]).resolve()))
