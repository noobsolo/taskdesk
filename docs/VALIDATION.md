# Validation — 2026-10-13

- Installed source: `/Users/eke/solo/taskdeck`; global launcher: `/Users/eke/.local/bin/taskdeck`; version 0.1.0.
- Installed tests: `.venv/bin/python -m pytest -q` — 21 passed in 10.45s.
- Uses real temporary Git repositories and worktrees, including paths with spaces and a 120-task dependency chain.
- Tests two concurrent manually started offline protocol sessions, steering, interruption, approval/input, resume, and leaving the dashboard while the service remains alive.
- Tests real Textual mouse/keyboard navigation, filtering, conversation entry, task creation, and a 100×30 terminal.
- Live Codex 0.162.1: initialize, thread/start, thread/read, thread/resume and thread/turns/list passed. No model turn started; empty test session archived.
- Global CLI version and check succeeded in `/private/tmp/taskdeck-demo`: 8 tasks, valid graph, available Codex, baseline exists, service not running.
- Actual TUI launched in a PTY; exit with Ctrl+Q returned 0. Screenshot in `taskdeck.png` comes from the actual Textual dashboard; SVG text positions account for terminal CJK cell widths.
- GitHub publication flow checked with response fixtures, not with a live PR. Actual model task execution and GitHub push/PR publication have not been performed.
- Local socket binding is prohibited by the Codex shell sandbox. Background-process integration and installed tests passed with host permissions; production code was not weakened to bypass this restriction.
