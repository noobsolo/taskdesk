import asyncio
import sys

import pyte
from textual.app import App

from taskdeck.terminal import NativeTerminal, PtySession, TerminalScreen, key_bytes, screen_text


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
