import asyncio
import base64
import codecs
import json
from pathlib import Path
import sys

import pyte
import pytest
from rich.console import Console
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import App

from taskdeck.terminal import NativeTerminal, PtySession, TerminalScreen, key_bytes, screen_text, terminal_color


def original_screen_text(screen):
    """Committed renderer as an independent visual reference for this change."""
    result = Text(no_wrap=True, overflow="crop")
    for y in range(screen.lines):
        if y:
            result.append("\n")
        for x in range(screen.columns):
            char = screen.buffer[y][x]
            if not char.data:
                continue
            cursor = not screen.cursor.hidden and (x, y) == (screen.cursor.x, screen.cursor.y)
            result.append(char.data, Style(color=terminal_color(char.fg), bgcolor=terminal_color(char.bg),
                          bold=char.bold, italic=char.italics, underline=char.underscore,
                          reverse=bool(char.reverse) ^ cursor, strike=char.strikethrough))
    return result


def rendered_rows(text, columns):
    console = Console(width=columns, force_terminal=True, color_system="truecolor")
    return [list(Segment.simplify(row)) for row in console.render_lines(text, console.options)]


def test_cached_text_matches_original_rendering_after_edits_and_resize():
    screen = TerminalScreen(20, 5, lambda data: None)
    stream = pyte.Stream(screen)
    for output in ["\x1b[38;2;18;52;86;48;2;101;67;33;1;3;4m中文e\u0301\x1b[0m",
                   "\r\n\x1b[7;9m反色\x1b[0m", "\x1b[1;1H改", "\x1b[3;1H光标",
                   "\x1b[?25l", "\x1b[?25h\x1b[2;3H", "\x1b[K", "\x1b[2J"]:
        stream.feed(output)
        expected = original_screen_text(screen)
        actual = screen_text(screen)
        assert actual.plain == expected.plain
        assert rendered_rows(actual, screen.columns) == rendered_rows(expected, screen.columns)
        # Appending to a returned Text must not mutate cached row content.
        actual.append("extra")
        assert screen_text(screen).plain == expected.plain
    stream.feed("\x1b[1;1H测试窗口缩放")
    for columns, lines in [(7, 3), (30, 8), (1, 1), (20, 5)]:
        screen.resize(columns=columns, lines=lines)
        assert rendered_rows(screen_text(screen), columns) == rendered_rows(original_screen_text(screen), columns)


def test_history_view_survives_output_and_cursor_queries_without_changing_live_buffer():
    replies = []
    screen = TerminalScreen(30, 6, replies.append)
    stream = pyte.Stream(screen)
    stream.feed("".join(f"{i:04d} 中文历史\r\n" for i in range(80)))
    live = screen.display[:]
    assert screen.scroll_history(-3)
    assert screen.scroll_offset == 3
    viewed = screen_text(screen).plain
    assert int(viewed[:4]) == int(live[0][:4]) - 3
    assert screen.display == live
    stream.feed("\x1b[6n")
    assert replies == [b"\x1b[6;1R"]
    assert screen_text(screen).plain == viewed
    stream.feed("\x1b[1;1H实时更新\x1b[6;1H\r\n新增输出\r\n")
    assert screen_text(screen).plain == viewed
    assert screen.scroll_offset == 5
    # Scrolling down traverses lines received while browsing, then rejoins live.
    screen.scroll_history(3)
    assert screen.scroll_offset == 2
    screen.scroll_history(3)
    assert screen.scroll_offset == 0
    assert rendered_rows(screen_text(screen), 30) == rendered_rows(original_screen_text(screen), 30)


@pytest.mark.parametrize("height", [3, 24, 40, 187])
def test_scroll_step_boundaries_and_resize_preserve_history_anchor(height):
    screen = TerminalScreen(20, height, lambda data: None)
    pyte.Stream(screen).feed("".join(f"{i:04d}\r\n" for i in range(400)))
    bottom = screen_text(screen).plain
    screen.scroll_history(-3)
    assert screen.scroll_offset == 3
    screen.resize(columns=12, lines=height)
    assert screen_text(screen).plain[:4] == f"{int(bottom[:4]) - 3:04d}"
    for _ in range(200):
        screen.scroll_history(-3)
    assert screen_text(screen).plain.startswith("0000")
    for _ in range(200):
        screen.scroll_history(3)
    assert screen.scroll_offset == 0
    assert screen_text(screen).plain[:4] == bottom[:4]


def test_history_eviction_and_clear_do_not_mutate_the_frozen_view():
    screen = TerminalScreen(20, 4, lambda data: None)
    stream = pyte.Stream(screen)
    stream.feed("".join(f"{i:04d}\r\n" for i in range(50)))
    screen.scroll_history(-20)
    viewed = screen_text(screen).plain
    stream.feed("".join(f"{i:04d}\r\n" for i in range(50, 2100)))
    assert len(screen.history.top) == 2000
    assert screen_text(screen).plain == viewed
    screen.scroll_history(-3)
    assert screen.scroll_offset == 2000
    screen.resize(columns=30, lines=6)
    assert len(screen_text(screen).plain.splitlines()) == 6
    stream.feed("\x1b[3J")
    assert screen.scroll_offset == 0
    assert not screen.history.top


def test_real_codex_trace_matches_five_captured_original_frames():
    fixture = json.loads((Path(__file__).parent / "fixtures/codex-terminal.json").read_text())
    snapshots = {frame["event_count"]: frame for frame in fixture["snapshots"]}
    screen = TerminalScreen(1, 1, lambda data: None)
    stream = pyte.Stream(screen)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    checked = []
    for count, event in enumerate(fixture["events"], 1):
        if event["kind"] == "resize":
            screen.resize(columns=max(1, event["columns"]), lines=max(1, event["lines"]))
        else:
            stream.feed(decoder.decode(base64.b64decode(event["data"])))
        if count in snapshots:
            console = Console(width=screen.columns, force_terminal=True, color_system="truecolor")
            rows = [[(segment.text, str(segment.style)) for segment in Segment.simplify(row)]
                    for row in console.render_lines(screen_text(screen), console.options, pad=False)]
            expected = [[tuple(segment) for segment in row] for row in snapshots[count]["rows"]]
            assert rows == expected, snapshots[count]["label"]
            checked.append(snapshots[count]["label"])
    assert checked == ["connected", "status-history", "chinese-input", "slash-menu", "resized"]


@pytest.mark.parametrize("paste", [False, True])
async def test_real_pty_output_keeps_history_fixed_and_input_returns_live(tmp_path, paste):
    captured = tmp_path / "input"
    script = """import os,tty,threading,time
tty.setraw(0)
os.write(1, ''.join(f'{i:04d} 中文历史\\r\\n' for i in range(120)).encode()+b'READY')
def output():
    time.sleep(.2)
    for i in range(100):
        os.write(1, f'\\x1b[2;1Hprogress {i:04d}\\x1b[6n'.encode())
        time.sleep(.01)
threading.Thread(target=output,daemon=True).start()
received=b''
while True:
    received+=os.read(0,1024)
    if b'x' in received or '粘贴'.encode() in received:
        open(CAPTURE,'wb').write(received)
        time.sleep(.2)
        break
""".replace("CAPTURE", repr(str(captured)))
    class TestApp(App):
        def compose(self):
            yield NativeTerminal([sys.executable, "-c", script], tmp_path)
    app = TestApp()
    async with app.run_test(size=(80, 24)) as pilot:
        terminal = app.query_one(NativeTerminal)
        for _ in range(50):
            if "READY" in screen_text(terminal.session.screen).plain:
                break
            await pilot.pause(.03)
        assert "READY" in screen_text(terminal.session.screen).plain
        terminal.post_message(events.MouseScrollUp(terminal, 1, 1, 0, -1, 0, False, False, False))
        await pilot.pause(.03)
        screen = terminal.session.screen
        assert screen.scroll_offset == 3
        viewed = screen_text(screen).plain
        await pilot.pause(.4)
        assert "progress" in "\n".join(screen.display)
        assert screen_text(screen).plain == viewed
        terminal.focus()
        if paste:
            terminal.post_message(events.Paste("粘贴"))
        else:
            await pilot.press("x")
        for _ in range(50):
            if captured.exists():
                break
            await pilot.pause(.03)
        received = captured.read_bytes()
        assert (b"\x1b[200~" + "粘贴".encode() + b"\x1b[201~" if paste else b"x") in received
        assert screen.scroll_offset == 0


async def test_identical_pty_resize_does_not_signal_child_again(tmp_path, monkeypatch):
    import taskdeck.terminal as native
    session = PtySession([sys.executable, "-c", "import time; time.sleep(10)"], tmp_path, 80, 24, lambda: None)
    calls = []
    ioctl = native.fcntl.ioctl
    def record(fd, request, data):
        calls.append(request)
        return ioctl(fd, request, data)
    monkeypatch.setattr(native.fcntl, "ioctl", record)
    try:
        assert session.resize(80, 24) is False
        assert not calls
        assert session.resize(90, 30) is True
        assert calls == [native.termios.TIOCSWINSZ]
        assert session.resize(90, 30) is False
        assert len(calls) == 1
    finally:
        await session.close()


def test_bounded_pty_reads_preserve_split_utf8_and_notify_once_per_callback(monkeypatch):
    import taskdeck.terminal as native
    payload = ("中文输出" * 100 + "END").encode()
    chunks = [payload[i:i + 13] for i in range(0, len(payload), 13)]
    session = object.__new__(PtySession)
    session.master = 42
    session.decoder = codecs.getincrementaldecoder("utf-8")("replace")
    session.screen = TerminalScreen(100, 20, lambda data: None)
    session.stream = pyte.Stream(session.screen)
    changed = []
    session.changed = lambda: changed.append(True)
    reads = []
    def read(fd, size):
        assert fd == 42
        if not chunks:
            raise BlockingIOError
        reads.append(size)
        return chunks.pop(0)
    monkeypatch.setattr(native.os, "read", read)
    while chunks:
        before_reads, before_changed = len(reads), len(changed)
        session.read()
        assert 1 <= len(reads) - before_reads <= 4
        assert len(changed) - before_changed == 1
    text = screen_text(session.screen).plain
    assert "END" in text
    assert text.count("中") == 100 and text.count("文") == 100
    assert "�" not in text


async def test_hidden_terminal_defers_refresh_until_shown(tmp_path, monkeypatch):
    class TestApp(App):
        def compose(self):
            yield NativeTerminal([sys.executable, "-c", "import time; print('READY',flush=True); time.sleep(10)"], tmp_path)
    app = TestApp()
    async with app.run_test(size=(80, 24)) as pilot:
        terminal = app.query_one(NativeTerminal)
        await pilot.pause(.15)
        terminal.display = False
        await pilot.pause()
        calls = []
        refresh = terminal.refresh
        def tracked(*args, **kwargs):
            calls.append(True)
            return refresh(*args, **kwargs)
        monkeypatch.setattr(terminal, "refresh", tracked)
        terminal.session.stream.feed("\r\nBACKGROUND")
        terminal.mark_dirty()
        terminal.redraw()
        assert terminal.dirty and not calls
        terminal.display = True
        await pilot.pause(.1)
        assert calls and not terminal.dirty
        assert "BACKGROUND" in terminal.render().plain


def test_terminal_keys_and_native_cursor_queries():
    assert key_bytes("escape") == b"\x1b"
    assert key_bytes("enter") == b"\r"
    assert key_bytes("ctrl+c") == b"\x03"
    assert key_bytes("shift+enter") == b"\x1b[13;2u"
    assert key_bytes("ctrl+left") == b"\x1b[1;5D"
    assert key_bytes("alt+b") == b"\x1bb"
    assert key_bytes("x", "中") == "中".encode()
    replies = []
    screen = TerminalScreen(20, 3, replies.append)
    pyte.Stream(screen).feed("\x1b[93;43m原生界面\x1b[0m\x1b[6n")
    assert replies == [b"\x1b[1;9R"]
    assert screen_text(screen).plain.startswith("原生界面")


async def test_real_pty_forwards_escape_without_exiting_dashboard(tmp_path):
    class TestApp(App):
        def compose(self):
            yield NativeTerminal([sys.executable, "-c", "import os,tty; tty.setraw(0); print('READY',flush=True); data=os.read(0,20); print('KEY:'+data.hex(),flush=True)"], tmp_path)
    app = TestApp()
    async with app.run_test(size=(80, 24)) as pilot:
        terminal = app.query_one(NativeTerminal)
        terminal.focus()
        for _ in range(50):
            if "READY" in screen_text(terminal.session.screen).plain:
                break
            await pilot.pause(0.05)
        assert "READY" in screen_text(terminal.session.screen).plain
        await pilot.press("escape")
        for _ in range(50):
            if "KEY:1b" in screen_text(terminal.session.screen).plain:
                break
            await pilot.pause(0.05)
        assert "KEY:1b" in screen_text(terminal.session.screen).plain
        assert len(app.screen_stack) == 1


async def test_pty_resize_and_chinese_bracketed_paste(tmp_path):
    script = "import os,tty,fcntl,termios,struct; tty.setraw(0); print('READY',flush=True); data=os.read(0,200); size=struct.unpack('HHHH',fcntl.ioctl(0,termios.TIOCGWINSZ,b'0'*8)); print('SIZE:'+str(size[:2]),flush=True); print('PASTE:'+data.hex(),flush=True)"
    session = PtySession([sys.executable, "-c", script], tmp_path, 80, 24, lambda: None)
    try:
        for _ in range(50):
            if "READY" in screen_text(session.screen).plain:
                break
            await asyncio.sleep(0.05)
        session.resize(90, 30)
        message = b"\x1b[200~" + "测试输入".encode() + b"\x1b[201~"
        session.write(message)
        for _ in range(50):
            if "PASTE:" in screen_text(session.screen).plain:
                break
            await asyncio.sleep(0.05)
        result = screen_text(session.screen).plain
        assert "SIZE:(30, 90)" in result
        assert "PASTE:" + message.hex() in result
    finally:
        await session.close()
