"""Check real native CLI folder trust in an isolated project, with no model turn.

  .venv/bin/python docs/check_worktree_trust.py /private/tmp/taskdeck-trust-check

Uses a private CODEX_HOME and an unreachable local provider; no account or API
credential is read. Screenshots and the result stay in OUTPUT.
"""
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib

from textual.widgets import TabbedContent

from taskdeck.core import Project, git
from taskdeck.service import Service
import taskdeck.terminal as native
from taskdeck.tui import TaskDeck, WorktreeTrustModal


async def main(output):
    output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="taskdeck-trust-", dir="/private/tmp"))
    home = root / "codex-home"
    home.mkdir(mode=0o700)
    os.environ["CODEX_HOME"] = str(home)
    config = home / "config.toml"
    base = ('model = "local-trust-check"\nmodel_provider = "trust-check"\n'
            'approval_policy = "on-request"\nsandbox_mode = "read-only"\n'
            '[model_providers.trust-check]\nname = "Isolated trust check"\n'
            'base_url = "http://127.0.0.1:9/v1"\nwire_api = "responses"\n'
            'requires_openai_auth = false\n'
            '[tui]\nscreen_reader_detection_done = true\n'
            f'[projects.{json.dumps(str(root))}]\ntrust_level = "trusted"\n')
    config.write_text(base)
    config.chmod(0o600)
    git(root, "init", "-b", "main")
    git(root, "-c", "user.name=Trust Check", "-c", "user.email=check@example.invalid",
        "commit", "--allow-empty", "-m", "isolated trust check")
    project = Project.init(root)
    task = project.add("T001", "真实 CLI 目录信任检查")
    task.folder.rename(task.folder.with_name('任务 "quoted"'))
    task = project.task(task.id)
    path = project.ensure_worktree(task)
    service = Service(project)
    loop = asyncio.get_running_loop()
    original_rpc, original_ensure = native.rpc, native.ensure_service
    results = []
    try:
        # A named empty thread is persisted without starting a turn.
        await service.load_thread(task, path)
        thread = task.state["thread_id"]
        server_pid = service.codex.proc.pid
        native.ensure_service = lambda _: None
        native.rpc = lambda _, request: asyncio.run_coroutine_threadsafe(service.dispatch(request), loop).result(45)

        async def check(label, expected, selected=task, toggle=None):
            before = tomllib.loads(config.read_text())
            selected_path = selected.state["worktree"]
            selected_thread = selected.state["thread_id"]
            app = TaskDeck(project)
            async with app.run_test(size=(180, 54)) as pilot:
                await pilot.pause()
                app.selected = selected.id
                app.details()
                await pilot.click(app.query_one(TabbedContent).get_tab("codex-tab"))
                async def wait_terminal():
                    terminal, text = None, ""
                    for _ in range(200):
                        terminals = list(app.query(native.NativeTerminal))
                        if terminals and terminals[0].session:
                            terminal = terminals[0]
                            text = native.screen_text(terminal.session.screen).plain
                            if "Trust this folder?" in text or "Trust and continue" in text:
                                return "trust-prompt", terminal, text
                            if "Ask Codex to do anything" in text and "Resuming session" not in text:
                                return "connected", terminal, text
                            if terminal.session.closed:
                                break
                        await asyncio.sleep(0.1)
                    return None, terminal, text

                outcome, terminal, text = await wait_terminal()
                if toggle is not None:
                    original = terminal
                    assert outcome == ("trust-prompt" if toggle else "connected")
                    await pilot.click("#trust-worktrees")
                    await pilot.pause()
                    assert isinstance(app.screen, WorktreeTrustModal)
                    app.save_screenshot(label + "-dialog.svg", path=str(output))
                    await pilot.click("#confirm-trust")
                    await app.workers.wait_for_complete()
                    outcome, terminal, text = await wait_terminal()
                    if toggle:
                        assert terminal is not original and original.session.closed, "waiting client was not reconnected"
                    else:
                        assert terminal is original and not original.session.closed, "connected client was replaced"
                app.save_screenshot(label + ".svg", path=str(output))
                (output / (label + ".txt")).write_text(text)
                assert outcome == expected, f"{label}: expected {expected}, got {outcome}; see OUTPUT"
                expected_config = copy.deepcopy(before)
                if outcome == "connected" and selected_path not in expected_config.get("projects", {}):
                    expected_config.setdefault("projects", {}).setdefault(selected_path, {})["trust_level"] = "trusted"
                assert tomllib.loads(config.read_text()) == expected_config, "unrelated config changed"
                assert service.codex.proc.pid == server_pid, "app-server restarted"
                assert selected.state["thread_id"] == selected_thread, "thread changed"
                result = {"case": label, "outcome": outcome,
                          "trust_registered": expected_config["projects"].get(selected_path, {}).get("trust_level") == "trusted",
                          "same_server": True, "same_thread": True, "unrelated_config_unchanged": True,
                          "ui_toggle": toggle}
                results.append(result)
                print(json.dumps(result), flush=True)
                if outcome == "connected":
                    terminal.focus()
                    await pilot.press("slash", *list("status"), "enter")
                    await asyncio.sleep(0.8)
                    status = native.screen_text(terminal.session.screen).plain
                    (output / (label + "-status.txt")).write_text(status)
                    app.save_screenshot(label + "-status.svg", path=str(output))
                    assert "Read Only (Ask for approval)" in status, "sandbox or approval behavior changed"

        await check("default-disabled", "trust-prompt")
        await check("ui-enable-from-prompt", "connected", toggle=True)
        await check("enabled-reopened", "connected")
        config.write_text(base + f'[projects.{json.dumps(str(path))}]\ntrust_level = "untrusted"\n')
        # An explicit untrusted choice is already a completed onboarding
        # decision in Codex 0.162.1: it connects without a new folder prompt.
        # The invariant is that taskdeck must not turn it into trusted.
        await check("explicit-untrusted", "connected")
        assert tomllib.loads(config.read_text())["projects"][str(path)]["trust_level"] == "untrusted"
        config.write_text(base + f'[projects.{json.dumps(str(path), ensure_ascii=False)}]\ntrust_level = "trusted"\n')
        await check("ui-disable-keeps-saved-trust", "connected", toggle=False)
        other = project.add("T002", "关闭后新 worktree 保留提示")
        other_path = project.ensure_worktree(other)
        await service.load_thread(other, other_path)
        await check("disabled-new-worktree", "trust-prompt", other)
        for saved in project.scan().values():
            turns = await service.codex.rpc("thread/turns/list", {"threadId": saved.state["thread_id"], "limit": 100})
            assert not turns.get("data"), "unexpected model turn"
        result = {"cases": results, "model_turns": 0, "isolated_codex_home": True,
                  "unrelated_config_unchanged": True, "root": str(root)}
        (output / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
    finally:
        native.rpc, native.ensure_service = original_rpc, original_ensure
        if service.codex:
            for saved in project.scan().values():
                if saved.state.get("thread_id"):
                    await service.codex.rpc("thread/archive", {"threadId": saved.state["thread_id"]})
            await service.codex.close()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]).resolve()))
