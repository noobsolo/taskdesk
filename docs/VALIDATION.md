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

## Native Codex tab — 2026-10-13

- Replaced the custom conversation screen, composer and JSON request modal with the real Codex CLI running in a PTY inside the first detail tab. Tasks without a saved thread hide the tab; starting a task reveals and opens it. Switching tasks retains each task's CLI client.
- Both the task service and native CLI connect to the same local app-server over a user-private Unix WebSocket socket. Attachment resumes the existing thread without starting a model turn. Credentials remain managed by Codex.
- Automated checks cover dynamic visibility, first-tab ordering, native key ownership, Ctrl+G focus return, cached clients across task selection, real PTY Escape/Chinese paste/resize, existing-thread attachment, idle recovery, and the existing task/worktree/PR behavior.
- Final full suite with host permissions for local socket tests: `.venv/bin/python -m pytest -q` — 42 passed in 17.96s. `git diff --check` passed. The installed global launcher uses this checkout's `.venv/bin/python -m taskdeck`.
- Live Codex 0.162.1 in an isolated temporary repository: the native folder trust dialog and conversation rendered inside the homepage tab; Chinese input appeared in the native composer. After sending a no-file-change test request, the service observed its real turn on the original thread; Escape changed that turn to interrupted without leaving the tab. Test threads were archived and test services closed.
- Actual screenshot exported from the native process: `/private/tmp/taskdeck-native.svg.png`. The final preview shows unsent Chinese input and starts no model turn.
- Existing k12 service was inspected read-only and left running because T007 was active. The new native transport takes effect after the old project service and dashboard are restarted when its running work has finished. The tab displays a clear upgrade message if it encounters the old service.

## Dashboard action cleanup — 2026-10-13

- Deleted the Enter session, New and View changes buttons, their dashboard action handlers, the new-task modal, the n/Enter dashboard shortcuts, and the changes tab/loader. Existing sessions open by clicking the Codex tab; starting a task still opens its native session.
- The remaining Start, Confirm completion, Open PR and Sync actions fit on one row. Existing 80×24 completion-control checks and native-tab input/client-retention checks passed. CLI task registration, diff and chat commands remain available.
- Full existing suite with host permissions: `.venv/bin/python -m pytest -q` — 41 passed in 16.53s; removed the obsolete new-task-modal test. `git diff --check` passed.
- Regenerated `taskdeck.svg` and `taskdeck.png` from the actual 140×42 Textual dashboard and visually verified the four-button layout. No model turn or actual project service restart was required.

## Embedded CLI scrolling — 2026-10-13

- Withdrawn after the user reported a corrupted UI in an actual Codex session. The synthetic row-construction benchmark below does not establish end-to-end scrolling performance or Codex display compatibility. The renderer, PTY scheduling, wheel changes and their added tests have been restored to the committed version; the experiment is archived at `/private/tmp/taskdeck-scroll-attempt`.
- Traced the wheel handlers to pyte's half-screen pagination and the renderer to rebuilding a Rich style/span for every visible character on each repaint. Refreshes were capped at 25 Hz and PTY reads drained output without a per-callback limit.
- The withdrawn experiment changed wheel ticks to 3 lines, cached immutable row content and grouped style runs, used Textual's line rendering and dirty-row refreshes including cursor-only changes, and scheduled redraw checks at 60 Hz.
- Historical experimental benchmark: median of 80 identical 3-line history movements; includes history movement and row construction, excludes Textual composition, terminal output, and physical trackpad latency:

  ```text
  viewport  old_ms  new_ms  speedup (median scroll + row construction)
  70x24     4.18    0.18   23.5x
  100x40    10.75    0.40   27.2x
  150x50    20.60    0.72   28.5x
  ```

- The withdrawn version passed 47 automated checks and a synthetic ANSI transcript screenshot check. Those checks missed the user-reported display failure. Next renderer work requires replay of actual Codex output and full-screen layout comparison.
- Installed launcher points to this checkout. Restart only the dashboard with `Ctrl+Q`, then `taskdeck`, to load the restored renderer. The user's project service and sessions have not been stopped.
- Restored suite with host permissions: `.venv/bin/python -m pytest -q` — 41 passed in 16.61s; `git diff --check` passed. Production code and tests now match the committed baseline.
- Separate scroll-state reproduction on the restored code: after scrolling a 24-line viewport upward, top row was `0125` and history position `1988`; feeding only the CLI cursor query `ESC[6n` moved the viewport to row `0137` and history position `2000` (bottom). This proves output/query handling can cancel history browsing; it does not by itself establish the cause of every reported lag or UI corruption.
- Proposed next work: collect actual Codex PTY output and measure the full event-to-display path; preserve the existing Rich/Textual full-screen rendering API while caching styles and merging equivalent spans; separate the history viewport from the live CLI buffer; evaluate refresh/read budgets and redundant resize notifications only with measured evidence. Actual Codex output, CJK/cursor/menu/resize equivalence and active-output scrolling are required before adopting another renderer change.

## Scrolling optimization with real CLI output — 2026-10-13

- Production still returns Rich `Text` through the existing `Widget.render()` path. It does not override `render_line()` or implement partial-region drawing. Cached immutable row text and shared styles reduce repeated per-character construction while Textual retains layout, cropping, backgrounds and composition.
- The history view now uses an absolute row anchor and copies only its visible cells. The live pyte screen continues receiving output and answering cursor queries. Output does not change the displayed history view; scrolling down traverses newly received lines, and keyboard/paste input returns to the live screen. Wheel steps are 3 rows and request a normal full refresh immediately instead of waiting for the 40 ms polling tick.
- PTY callbacks consume up to four 8 KiB chunks and check a 4 ms work budget after each chunk. UTF-8 decoder state spans callbacks; unread bytes remain in the PTY. Hidden terminals defer redraw, and identical PTY dimensions no longer send a redundant resize signal.
- Real Codex CLI 0.162.1 probe, same TaskDeck layout at 180×54 (embedded viewport 97×39), 12 local `/status` commands, unsent Chinese input, slash menu, resize to 140×42 and back, then 20 wheel events. Neither probe started a model turn; their empty test threads were archived and servers closed. Commands:

  ```sh
  .venv/bin/python docs/check_terminal_scroll.py /private/tmp/taskdeck-scroll-baseline --baseline
  .venv/bin/python docs/check_terminal_scroll.py /private/tmp/taskdeck-scroll-final
  ```

  | Measurement | Baseline `a9def48` | Updated renderer |
  | --- | ---: | ---: |
  | Raw real CLI output | 130,623 bytes | 130,021 bytes |
  | Median `Widget.render()` construction | 6.02 ms | 0.35 ms |
  | Median wheel dispatch to Textual composition | 52.08 ms | 3.42 ms |
  | Wheel samples | 20 | 20 |
  | Model turns | 0 | 0 |

- The wheel metric includes event handling, scheduling and composition in Textual's headless test driver; it excludes physical trackpad input, terminal-emulator output and display latency. It is not a guarantee of that exact latency in the user's terminal. The original wheel step was half a viewport; the updated step is 3 rows.
- `--baseline` loads the pinned pre-optimization commit `a9def48eff857ff24081c6ff55063737b19622b1`, so the same comparison remains reproducible after this change is committed.
- Replayed the baseline capture and verified all five original frames exactly before sanitizing identifiers. `tests/fixtures/codex-terminal.json` contains 318 real PTY/resize events and five original-renderer frames; email/home/session identifiers are replaced without changing byte/cell width, including identifiers split across read chunks. Automated replay compares rendered text/style segments at connection, status history, Chinese input, slash menu and resize.
- Reviewed actual updated CLI screenshots at `/private/tmp/taskdeck-scroll-final/chinese-input.svg.png`, `slash-menu.svg.png`, `resized.svg.png` and `scroll-up.svg.png`: CJK text, native menu highlighting, cursor, status columns, footer and resized content stay inside the existing pane.
- Full suite with host permissions for the temporary Unix socket: `.venv/bin/python -m pytest -q` — 54 passed in 18.10s. Coverage includes real PTY ongoing output/cursor reports while browsing, key/paste return to live, history bounds/eviction, resize, split UTF-8, bounded read callbacks, hidden tab restoration and existing task/service/PR behavior. `git diff --check` passed.
- Only the dashboard needs `Ctrl+Q` and `taskdeck` to load this update. No user project service was restarted and no user task was started or interrupted. Physical scrolling in the user's already-running dashboard still requires reloading that process.

## Project worktree trust — 2026-10-14

- Added `taskdeck trust-worktrees enable|disable|status`, off by default. The opt-in is local to the Git common directory and bound to project identity/root and current `CODEX_HOME`; a checked-in project setting cannot enable it. Only registered worktrees at the task's recorded managed path, belonging to the same Git repository, qualify.
- Real Codex 0.162.1 verification found that a CLI `-c projects.<path>.trust_level="trusted"` override does not skip folder onboarding with `resume --remote`. The implementation instead uses the running server's `config/read` and `config/value/write` API, writing only that worktree's trust key with the user config layer's `expectedVersion`. It checks explicit untrusted settings and repeats the local authorization/path check before writing. It refuses mismatched server config directories and endpoints.
- Full suite: `.venv/bin/python -m pytest -q` — 71 passed in 20.79s with host permissions for temporary Unix sockets. The 17 new checks cover local opt-in, quoted/CJK paths, enable/status/disable without model work, redirected/foreign/moved worktrees, project/Codex-home changes, explicit distrust, malformed config, config-version conflict, missing version, mismatched config directory/endpoint, and disabling during a config read. `git diff --check` passed.
- Final real CLI command: `.venv/bin/python docs/check_worktree_trust.py /private/tmp/taskdeck-trust-check-final`. Uses a temporary Git project, separate credential-free `CODEX_HOME`, and an unreachable local provider; no real user account configuration is modified. Output contains six native-tab screenshots, local `/status` screenshots/text, and `result.json`.

  | Case | Native CLI result | Saved trust |
  | --- | --- | --- |
  | Default disabled | Folder trust prompt | No worktree entry |
  | Enabled | Connected directly | Only selected worktree becomes trusted |
  | Enabled, dashboard reopened | Connected directly | Existing trusted entry retained |
  | Explicit untrusted | Connected under Codex's existing untrusted decision | Untrusted preserved |
  | Disabled, previously trusted worktree | Connected directly | Saved trust retained |
  | Disabled, new worktree | Folder trust prompt | No new entry |

- Codex treats an explicit untrusted entry as a completed folder choice, so preserving untrusted does not necessarily produce another trust prompt. The probe asserts that it is not converted to trusted. Every connected `/status` displays `Read Only (Ask for approval)`, and all unrelated configuration values remain unchanged.
- All six checks keep the same app-server process and preserve each task's original thread. Two empty test threads contain zero model turns and are archived on cleanup. The feature needs only a dashboard reload; no running user project service or task was restarted/interrupted. It has not been enabled for the user's projects automatically. Disabling stops new automatic registrations and retains already-saved Codex trust entries, as documented.

## Worktree trust button — 2026-10-14

- Added a project-wide `目录信任：开/关` button beside Import in the top filter row. The dialog displays project scope/current state and offers Enable/Disable plus Back/Escape. It writes the same local opt-in as the CLI; periodic refresh also reflects external CLI changes. The four task action buttons remain in their original bottom row.
- Enabling from the UI reconnects only the selected, visible native client that is still at the live folder-trust prompt. Connected clients, menus and unsent drafts are retained. The live buffer is inspected so an old prompt in scrollback cannot trigger a reconnect. Disabling stops new registrations and leaves already-connected clients and saved Codex trust intact.
- Real CLI probe updated to use the UI buttons: `.venv/bin/python docs/check_worktree_trust.py /private/tmp/taskdeck-trust-ui-final` — all six scenarios passed. Enabling replaces the waiting PTY client and connects directly; disabling retains the existing client. Same server process and original threads throughout, zero model turns, explicit untrusted and unrelated configuration preserved, and every connected `/status` remains `Read Only (Ask for approval)`.
- The real probe exposed a refresh/teardown race: the dashboard or modal can change while the snapshot thread is running. Reload now rechecks the screen and mounted controls after the await. A deterministic regression opens the trust dialog during an in-flight refresh; the final live probe also exits all six dashboards without errors.
- Full suite: `.venv/bin/python -m pytest -q` — 75 passed in 25.68s with host permissions for temporary sockets. Four new UI checks cover 80×24 empty-dashboard/button/dialog bounds, cancellation, enable/disable persistence without starting tasks, initial/external state, rejection of an explicitly untrusted project, and concurrent modal/refresh behavior. Existing native-client retention and bottom-action layout checks also pass. `git diff --check` passed.
- Regenerated `docs/taskdeck.svg` and `docs/taskdeck.png` from the actual 140×42 demo dashboard. Visually inspected the wide dashboard, 80×24 dashboard and trust dialog rendered by Quick Look; new controls fit the existing layout. Preview tasks are isolated and no model work is started.
- The user's k12 opt-in remains enabled. Only a dashboard reload is needed to show the new button; its project service and running tasks were not restarted or interrupted.
