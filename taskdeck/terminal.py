"""A PTY viewport for the real Codex CLI, without a second chat implementation."""
from __future__ import annotations

import asyncio
import codecs
import errno
import fcntl
import os
from pathlib import Path
import pty
import struct
import subprocess
import sys
import termios

import pyte
from rich.style import Style
from rich.text import Text
from textual import events
from textual.widget import Widget
from textual.widgets import Static

from .core import DeckError
from .service import ensure_service, rpc


def key_bytes(key, character=None):
    """Encode terminal keys; Codex alone decides what these keys do."""
    keys = {"escape": b"\x1b", "enter": b"\r", "backspace": b"\x7f", "tab": b"\t",
            "shift+tab": b"\x1b[Z", "up": b"\x1b[A", "down": b"\x1b[B",
            "right": b"\x1b[C", "left": b"\x1b[D", "home": b"\x1b[H", "end": b"\x1b[F",
            "delete": b"\x1b[3~", "insert": b"\x1b[2~", "pageup": b"\x1b[5~", "pagedown": b"\x1b[6~",
            "shift+enter": b"\x1b[13;2u", "ctrl+enter": b"\x1b[13;5u", "alt+enter": b"\x1b\r"}
    if key in keys:
        return keys[key]
    if key.startswith("ctrl+") and len(key) == 6:
        char = key[-1]
        if "a" <= char <= "z":
            return bytes([ord(char) - ord("a") + 1])
        if char in "@ [\\]^_":
            return bytes([ord(char) & 31])
    if key.startswith("alt+"):
        rest = key[4:]
        value = key_bytes(rest, character if character and len(character) == 1 else rest if len(rest) == 1 else None)
        return b"\x1b" + value if value else b""
    for direction, suffix in (("up", "A"), ("down", "B"), ("right", "C"), ("left", "D"), ("home", "H"), ("end", "F")):
        if key.endswith("+" + direction):
            parts = key.split("+")[:-1]
            modifier = 1 + ("shift" in parts) + 2 * ("alt" in parts) + 4 * ("ctrl" in parts)
            return f"\x1b[1;{modifier}{suffix}".encode()
    if key.startswith("f") and key[1:].isdigit():
        n = int(key[1:])
        if 1 <= n <= 4:
            return b"\x1bO" + bytes([ord("P") + n - 1])
        if 5 <= n <= 12:
            return f"\x1b[{(15, 17, 18, 19, 20, 21, 23, 24)[n - 5]}~".encode()
    return character.encode() if character else b""


class TerminalScreen(pyte.HistoryScreen):
    def __init__(self, columns, lines, write):
        self.write = write
        super().__init__(columns, lines, history=2000)

    def write_process_input(self, data):
        self.write(data.encode())


class PtySession:
    def __init__(self, command, cwd, columns, lines, changed):
        self.changed = changed
        self.master, slave = pty.openpty()
        self.loop = asyncio.get_running_loop()
        self.pending_input = bytearray()
        self.screen = TerminalScreen(max(1, columns), max(1, lines), self.write)
        self.stream = pyte.Stream(self.screen)
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.closed = False
        self.exit_code = None
        self.resize(columns, lines)
        env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor")
        env.pop("NO_COLOR", None)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
        # A tiny child sets its controlling tty before exec; no preexec_fn runs
        # in this multithreaded dashboard and no shell interprets the command.
        try:
            self.proc = subprocess.Popen([sys.executable, "-m", "taskdeck.terminal", "--exec", *command],
                                         cwd=cwd, env=env, stdin=slave, stdout=slave, stderr=slave,
                                         start_new_session=True, close_fds=True)
        except BaseException:
            os.close(self.master)
            raise
        finally:
            os.close(slave)
        os.set_blocking(self.master, False)
        self.loop.add_reader(self.master, self.read)

    def resize(self, columns, lines):
        columns, lines = max(1, columns), max(1, lines)
        self.screen.resize(lines=lines, columns=columns)
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", lines, columns, 0, 0))

    def read(self):
        try:
            while True:
                data = os.read(self.master, 65536)
                if not data:
                    self.finish()
                    return
                self.stream.feed(self.decoder.decode(data))
                self.changed()
        except BlockingIOError:
            return
        except OSError as e:
            if e.errno == errno.EIO:
                self.finish()
            else:
                raise

    def write(self, data):
        if self.closed:
            return
        self.pending_input.extend(data)
        self.flush_input()

    def flush_input(self):
        try:
            while self.pending_input:
                count = os.write(self.master, self.pending_input)
                del self.pending_input[:count]
            self.loop.remove_writer(self.master)
        except BlockingIOError:
            self.loop.add_writer(self.master, self.flush_input)
        except OSError:
            self.finish()

    def finish(self):
        if self.closed:
            return
        self.closed = True
        self.loop.remove_reader(self.master)
        self.loop.remove_writer(self.master)
        os.close(self.master)
        self.exit_code = self.proc.poll()
        self.changed()

    async def close(self):
        # Only detach the CLI client. The independent app-server owns the turn.
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                await asyncio.to_thread(self.proc.wait, 3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                await asyncio.to_thread(self.proc.wait)
        self.finish()


def terminal_color(value):
    if value == "default":
        return None
    value = value.replace("bfight", "bright").replace("brown", "yellow")
    if value.startswith("bright"):
        return "bright_" + value[6:]
    if len(value) == 6 and all(c in "0123456789abcdef" for c in value):
        return "#" + value
    return value


def screen_text(screen):
    result = Text(no_wrap=True, overflow="crop")
    for y in range(screen.lines):
        if y:
            result.append("\n")
        for x in range(screen.columns):
            char = screen.buffer[y][x]
            if not char.data:
                continue
            color = terminal_color(char.fg)
            background = terminal_color(char.bg)
            cursor = not screen.cursor.hidden and (x, y) == (screen.cursor.x, screen.cursor.y)
            style = Style(color=color, bgcolor=background, bold=char.bold, italic=char.italics,
                          underline=char.underscore, reverse=bool(char.reverse) ^ cursor, strike=char.strikethrough)
            result.append(char.data, style)
    return result


class NativeTerminal(Widget, can_focus=True):
    DEFAULT_CSS = "NativeTerminal { height: 1fr; width: 1fr; padding: 0; background: #10171c; }"

    def __init__(self, command, cwd, **kwargs):
        super().__init__(**kwargs)
        self.command, self.cwd = command, cwd
        self.session = None
        self.dirty = True

    def on_mount(self):
        self.session = PtySession(self.command, self.cwd, self.size.width, self.size.height, self.mark_dirty)
        self.set_interval(0.04, self.redraw)

    async def on_unmount(self):
        if self.session:
            await self.session.close()

    def mark_dirty(self):
        self.dirty = True

    def redraw(self):
        if self.dirty:
            self.dirty = False
            self.refresh()

    def on_resize(self, event):
        if self.session and not self.session.closed and event.size.width and event.size.height:
            self.session.resize(event.size.width, event.size.height)
            self.mark_dirty()

    def render(self):
        return screen_text(self.session.screen) if self.session else Text("正在连接 Codex…")

    def on_key(self, event: events.Key):
        if event.key in {"ctrl+q", "ctrl+g"}:
            return
        event.stop()
        event.prevent_default()
        if self.session:
            self.session.screen.next_page()
            self.session.write(key_bytes(event.key, event.character))

    def on_paste(self, event: events.Paste):
        event.stop()
        if self.session:
            self.session.write(b"\x1b[200~" + event.text.encode() + b"\x1b[201~")

    def on_click(self):
        self.focus()

    def on_mouse_scroll_up(self, event):
        event.stop()
        if self.session:
            self.session.screen.prev_page()
            self.mark_dirty()

    def on_mouse_scroll_down(self, event):
        event.stop()
        if self.session:
            self.session.screen.next_page()
            self.mark_dirty()


class CodexPane(Widget):
    DEFAULT_CSS = """
    CodexPane { height: 1fr; width: 1fr; }
    CodexPane > Static { height: auto; }
    """

    def __init__(self, project, **kwargs):
        super().__init__(**kwargs)
        self.project = project
        self.ident = None
        self.terminals = {}
        self.attaching = set()

    def compose(self):
        yield Static("", id="codex-connection", markup=False)

    async def select_task(self, ident):
        if not ident or not self.project.task(ident).state.get("thread_id"):
            return
        self.ident = ident
        for task_id, terminal in self.terminals.items():
            terminal.display = task_id == ident
        message = self.query_one("#codex-connection", Static)
        message.update("")
        message.display = False
        if ident in self.terminals:
            terminal = self.terminals[ident]
            if terminal.session and terminal.session.closed:
                await terminal.remove()
                self.terminals.pop(ident)
            else:
                terminal.focus()
                return
        if ident in self.attaching:
            return
        self.attaching.add(ident)
        try:
            message.display = True
            message.update("正在连接 Codex…")
            await asyncio.to_thread(ensure_service, self.project)
            connection = await asyncio.to_thread(rpc, self.project, {"op": "attach", "task": ident})
            terminal = NativeTerminal(connection["command"], connection["cwd"])
            # Child Python must still find this installed editable package when
            # the PTY changes cwd to the task's worktree.
            terminal.display = self.ident == ident
            self.terminals[ident] = terminal
            await self.mount(terminal)
            if self.ident == ident:
                message.display = False
                terminal.focus()
        except DeckError as e:
            if self.ident == ident:
                message.display = True
                detail = str(e)
                if "未知操作：attach" in detail:
                    detail = "任务服务还是旧版本。请在运行中任务结束后执行 taskdeck service stop，再重新打开面板接入原生会话。"
                message.update(detail)
        finally:
            self.attaching.discard(ident)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--exec":
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        os.execvp(sys.argv[2], sys.argv[2:])
    raise SystemExit("PTY child requires --exec COMMAND")
