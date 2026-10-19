# taskdeck

A local terminal task console. Each task folder maps to one Git worktree, one branch, one real Codex session, and one PR.

Every task starts manually. Creating tasks, browsing, filtering, viewing sessions, and refreshing dependencies never start the model; a task whose dependencies are met simply becomes "ready to start." Multiple manually started tasks can run in parallel.

<img src="docs/taskdeck.svg" alt="taskdeck task console interface" width="1200">

## Usage

The installed command is `taskdeck`. Run it in any Git project or one of its subdirectories:

```sh
taskdeck init --base main
taskdeck add T001 "Build the foundation"
taskdeck add T002 "Implement the database" --depends T001
taskdeck edit T001
taskdeck
```

`init` prefers the remote default branch, then local `main`/`master`, and finally falls back to the current branch; you can also pass `--base main`. A new repository needs at least one commit before worktrees can be created. Initialization writes `.taskctl.yaml`, the task directory, and local run-directory ignore rules to `.gitignore`; it does not commit anything for you.

The panel supports mouse buttons and arrow keys for selecting tasks. `s` starts, `/` searches, `r` syncs the baseline and PRs, and `Ctrl+Q` exits the panel. When a task already has a session, the first tab on the right is "Codex"; click it to enter the session. Tasks that have not started hide the tab. The session is the native terminal UI of the local Codex CLI, so Enter, Esc, arrow keys, slash commands, paste, and approvals are all handled by Codex: Enter sends, and Esc interrupts the current turn while it is running. `Ctrl+G` moves focus back to the task list so you can switch tasks; you can also click another tab directly. Typing works normally while the input box or editor has focus.

The action row has only four buttons — "Start", "Confirm Complete", "Open PR", and "Sync" — laid out on a single line that fits an 80 × 24 terminal; a wider terminal helps with long task titles. To add a task, run `taskdeck add`; to view Git changes, run `taskdeck diff`.

Projects that already have `docs/tasks/Txxx-*.md` can click "Import Tasks" in the panel, or run:

```sh
taskdeck import docs/tasks --dry-run
taskdeck import docs/tasks
```

Import extracts IDs, titles, dependencies, phases, and acceptance checklists from top-level headings and the task table, and stores the full original text in each task's `description.md`. Source files are left untouched, and repeated imports preserve existing tasks and user edits. Importing does not start Codex.

Tasks marked "completed" in the original document appear as "Needs review." For regular development tasks, you can also select one and click "Confirm Complete" at the bottom, or press `c` in the task list, to verify the actual delivery commit. Development completion and session state are stored separately: you can confirm delivery while the session is running, waiting, or idle, and confirming neither interrupts nor closes the session. If the code has not yet reached the target baseline, the task shows "Completed, pending baseline"; dependencies are released only after it lands in the baseline. The "Start" button is disabled when no task is selected, and an empty list shows a hint to import tasks or run `taskdeck add`.

## Task directory

```text
project/
  .taskctl.yaml
  tasks/
    T001/
      task.yaml
      description.md
      delivery.md              # Generated on the task branch, committed with the PR
      .local/                  # Git-ignored
        state.json             # State, plan, branch, PR, delivery commit
        session.json           # Real Codex thread ID
        transcript.json        # Persisted conversation and command results
        events.jsonl           # Manual start, steer, interrupt, failure, and other events
        worktree/              # Isolated source working directory
```

Example `task.yaml`:

```yaml
id: T002
title: Implement the database
depends_on: [T001]
acceptance:
  - Database initializes successfully
  - Data migration tests pass
```

Only `task.yaml` files in direct subdirectories of the configured task directory are scanned; tasks are identified by their manifest `id`, and worktrees are not traversed. IDs are unique within a repository but may repeat across repositories. Duplicate IDs, missing dependencies, cyclic dependencies, and directory escapes are all reported as errors. Manifests and descriptions can be committed; runtime state, logs, and worktrees stay on the local machine.

Task folders can be renamed as long as the ID stays the same. Stop the task before renaming; the next manual start repairs the worktree pointer and preserves the session. The configured target branch is a project-level setting shared by dependency gates and PRs.

Project registration lives in `taskdeck/project.json` under the Git shared management directory. Starting from a project subdirectory or a linked worktree locates the same set of tasks. Local session state is independent after moving to another machine or cloning fresh; you can keep `completed_commit` in manifests to share evidence of delivered commits.

## Sessions and the background service

The execution layer is the local `codex app-server`, reached over a user-private Unix socket. The home "Codex" tab runs the real `codex resume --remote unix://... <thread_id>` in a PTY, connecting to the same thread the task already has; session content, the input box, menus, and approvals are rendered by Codex itself, and the panel does not implement a separate chat page. Switching tasks preserves each task's terminal client; exiting the panel only detaches the client, while the independent service keeps running. By default it reuses the logged-in Codex model, sandbox, and approval configuration and does not handle API keys or OAuth tokens. Native integration requires a Codex CLI that supports `--remote` and Unix sockets; the locally verified version is 0.162.1. See the [official native terminal integration guide](https://learn.chatgpt.com/docs/app-server#connect-the-cli-terminal-ui).

The background service starts on the first manual start/send or when attaching to an existing session, and keeps running after you exit the panel. Browsing tasks alone does not start the Codex service; opening an existing session only restores the connection and does not begin a model turn. After a service restart or disconnect, you can reconnect to the original session and keep typing; it does not rerun automatically.

Native sessions follow Codex's own message queueing/steering and keyboard behavior. The CLI's `taskdeck send` uses `turn/steer` while a turn is running and `turn/start` when idle; `taskdeck stop` uses `turn/interrupt`. A task shows as interrupted only after the completion notification arrives. File changes already made are kept. Interrupting a turn is not the same as cleaning up every background shell Codex started.

Approvals and questions use the native interactions inside the Codex session; the panel does not add separate JSON request dialogs. A completed model turn is marked only as "waiting for you" and is not taken as proof that tests passed or the task merged.

The Codex tab's scroll wheel moves 3 lines per step. When you scroll up through history, new output keeps arriving while the view stays where you are reading; scroll back to the bottom, or start typing or pasting, to return to the live session. After a terminal display-layer update, press `Ctrl+Q` to exit and rerun `taskdeck` to load it; there is no need to stop the background task service.

When upgrading from an older version, an existing background service is still running the old code. Wait for running tasks to finish, run `taskdeck service stop`, then press `Ctrl+Q` to exit and rerun `taskdeck`. Entering an original session connects to the new local endpoint and continues using the same thread; it does not recreate the session or start tasks automatically.

```sh
taskdeck start T001
taskdeck chat T001
taskdeck send T001 "Check the failing tests first; hold off on UI changes"
taskdeck stop T001
taskdeck service status
taskdeck service stop
```

Stopping the background service disconnects this project's execution connection. To leave only the panel, use `Ctrl+Q`.

## Worktree directory trust

Codex records directory trust separately for each task's isolated worktree; even when the main repository is trusted, a new task may still show "Trust and continue." The "Directory trust: on/off" button next to "Import Tasks" at the top shows the project's current state. Click it to review the scope, then choose "Enable auto trust" or "Disable auto trust"; you can also run:

```sh
taskdeck trust-worktrees enable
taskdeck trust-worktrees status
# Revoke inheritance
taskdeck trust-worktrees disable
```

Off by default. Enabling allows these worktrees to load Codex project configuration, hooks, and rules; the record is stored only in `taskdeck/trust-worktrees.json` in the local Git shared management directory, bound to the project path and the current `CODEX_HOME`, and does not propagate through clones or project configuration.

The next time a native session connects, the panel verifies the task directory, Git repository, and registered worktrees, then uses Codex's native configuration interface to register only that directory's `projects.<path>.trust_level="trusted"` in the current `CODEX_HOME/config.toml`. It checks the configuration version before writing to avoid overwriting concurrent changes. Projects or worktrees explicitly marked `untrusted` in Codex configuration are not overwritten; existing `trusted` settings are preserved. Sandbox and command approval settings are not modified. See the [official OpenAI directory trust configuration](https://learn.chatgpt.com/docs/config-file/config-reference).

The UI toggle saves immediately. If a current session is stopped at the native directory Trust prompt, enabling reconnects that terminal client while keeping the original thread and background task. Other connected sessions stay as they are, and new connections apply the current setting. After toggling on the command line, the panel refreshes the button state; a client already stuck at the Trust prompt can exit the panel and reconnect without stopping the background service.

Disabling stops automatic registration; directory trust already saved in Codex is retained. To revoke it, set the corresponding directory to `untrusted`. Hooks' own authorization prompts are still handled by Codex.

## Dependencies and PRs

Before a task starts, it refreshes the target branch, checks that dependency delivery commits are actually in the baseline, and then checks whether the worktree contains those commits. Dependencies are never released based only on status text or the existence of a PR. Existing worktrees are not reset; if one is behind a new dependency, update the target branch in that directory first, then continue.

A task's initial instruction includes its scope, dependency checks, acceptance requirements, required verification, delivery records, commit/push instructions, and PR requirements. PRs are created or updated by that task's Codex session; the panel offers opening the PR and syncing status, but no manual create-PR button. "Confirm Complete" only records your delivery confirmation; it does not create a PR or close the session.

`taskdeck pr` is a delivery tool for Codex sessions to call. It requires an existing commit, a clean working directory, and a real `delivery.md`, and it will not write commits on Codex's behalf or assume verification passed. Sessions can also use existing GitHub tools to create or update PRs.

```sh
taskdeck diff T001
taskdeck sync
```

PR integration currently supports HTTPS/SSH remotes on `github.com`. It uses `GH_TOKEN`, `GITHUB_TOKEN`, or the Git credential helper and never writes credentials into task files. Missing credentials or network failures are reported as errors. Open PRs with the same base/head are reused; nothing is merged by default.

After merging, run "Sync" to obtain the real PR merge commit. Dependent tasks can start only once that commit has entered the target branch.

For existing tasks delivered without a PR, you can record the actual delivery commit manually:

```sh
taskdeck complete T001 --commit abc1234
```

The commit must exist; when the task has its own branch, the commit must belong to that branch. This is your manual delivery confirmation, not automated proof of verification; the tool does not run acceptance commands on its own. Deliveries that have not yet entered the baseline still keep the dependency gate; confirming does not rewrite the state of an in-progress session or turn.

## Installation and development

Python 3.12+, Git, and the Codex CLI. The current protocol has been checked against Codex 0.162.1. To use a different Codex, set the `codex_command` argument array in `.taskctl.yaml`; a model can be set explicitly with `model` and is not overridden by default.

```sh
uv venv --python 3.12
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -m pytest -q
```

macOS sandbox environments may prohibit binding Unix sockets; background service integration tests must run on a host environment that allows local sockets. Protocol fixtures are used only in `tests/`; production always calls the real Codex by default.

## Verification scope

Tests cover real Git repositories and worktrees, 120 tasks, dependency-graph errors, upstream commit gates, directory rename recovery, independent background processes, mouse/keyboard UI interaction, same-session steering and interruption, and GitHub PR request fixtures. Real Codex checks cover the handshake, session creation, reading, resumption, and history pagination; that check does not send model tasks.

The first real development task and GitHub publishing still depend on your project, Codex login, and repository permissions. The implementation phase did not automatically start your project tasks or publish real PRs for testing.

Protocol references: [Codex app-server](https://learn.chatgpt.com/docs/app-server), [Git worktree](https://git-scm.com/docs/git-worktree), [GitHub PR API](https://docs.github.com/en/rest/pulls/pulls), [Textual testing](https://textual.textualize.io/guide/testing/).
