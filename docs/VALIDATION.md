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

## Existing-task import and empty-state fix — 2026-10-13

- Added Markdown task import with dry-run, exact source preservation, dependency validation and idempotent skip of existing tasks.
- Empty task selection disables Start and shows the discovered legacy task documents; imported completed documents require explicit commit confirmation.
- Real k12 project: 119 task folders registered, all copied descriptions byte-equal to the original documents, zero task runtime folders and no running Codex service.
- Real k12 T119 dependency display reduced 36,414 repeated upstream path nodes to 324 visible lines by expanding shared dependencies once.
- PR target setting was preserved while the user chooses the intended target; no k12 task was started.

## Completion button visibility — 2026-10-13

- Reproduced the original clipping: in an 80-column terminal, the completion button occupied x=77..87.
- Changed the actions to a two-row, four-column grid; Confirm completion is the third button in the first row. Added c shortcut for the selected unverified task.
- Fixed the completion dialog's collision with Textual's read-only task property.
- Installed tests: 28 passed in 12.83s.
- Verified with real k12 data at 80×24: T001 selected, button x=40..59 and y=17..20, enabled and fully visible; both mouse click and c open the confirmation dialog. No completion was submitted.
- The user's existing dashboard process predates the UI update; restart only the dashboard to load the updated controls. No running k12 service or task was stopped.

## Delivery completion and session-owned PRs — 2026-10-13

- Installed full suite: 35 passed in 14.82s. Regression coverage includes confirming an active session without changing its thread/turn, preserving confirmation after turn completion, and enforcing actual baseline ancestry before unlocking dependencies.
- Removed the dashboard Create PR button. Task prompts instruct the Codex session to create/update its PR; the CLI PR helper remains available for the session.
- Existing PRs reported in a task worktree's delivery.md are checked against the repository and task identity, including reused PR heads different from the manager's task branch.
- GitHub API 2026-03-10 removes merge_commit_sha. Requests now explicitly use the supported 2022-11-28 contract so sync records the actual merge commit. Source: https://docs.github.com/en/rest/about-the-rest-api/breaking-changes
- Fresh k12 GitHub read confirmed T003 PR #2 merged into main, commit 69eba0b470d577c6401b6cd9397a7824d63377cd. Fetched baseline contains that commit; task shows 已入基线.
- Corrected k12 target_branch from the previous feature branch to main. The source checkout and task worktree were not switched or reset. Reloaded only the idle service; the saved T003 thread was preserved and no model turn or PR was created.
- Verified actual k12 UI data at 80×24: T003 selected, all action buttons visible, no Create PR widget, original session shown and Enter enabled. Confirm completion is disabled for this task because its PR is already in the baseline.
- Existing dashboard processes need Ctrl+Q and taskdeck to load the changed controls; exiting the dashboard preserves the task service and sessions.
