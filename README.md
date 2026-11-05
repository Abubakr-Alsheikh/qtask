# qtask

A local task tracker for AI coding agents and the people they work with. One Python file, standard library only, one SQLite database on your machine.

```text
$ qtask add "Fix the login redirect" --kind bug --pri P1
Created APP-14: Fix the login redirect
$ qtask claim APP-14 --agent fe-fix-1
Claimed APP-14 as fe-fix-1 until 2026-11-02T17:56:49Z. Status: in_progress.
$ qtask ask APP-14 "Keep the old URL working?" --to developer --agent fe-fix-1
Question #1 on APP-14 sent to developer. Status: needs_confirm.
$ qtask inbox --for developer
#1 on APP-14 [question, open] fe-fix-1 to developer: Keep the old URL working?
$ qtask answer 1 "Yes, redirect it" --agent developer
Answer #2 added. Question #1 resolved. APP-14 status: in_progress.
$ git commit -am "fix(auth): keep the old login URL as a redirect"
$ qtask commit APP-14 --agent fe-fix-1
Linked 1 commit to APP-14:
  3f9c2ab fix(auth): keep the old login URL as a redirect
$ qtask done APP-14 --evidence "npm test: 412 passed" --agent fe-fix-1
Done APP-14.
```

Status: **0.2, early.** It runs daily on one Windows laptop with several agents at once. Commands and output may still change before 1.0.

## Why another tracker

- **Nothing to install but Python.** No packages, no server, no account. Read the whole tool in one file before you trust it.
- **Built for agents.** Claims with leases stop two agents from taking the same task. Agents ask questions with `ask`, the developer answers, and the agent finds the answer in `inbox`. Closing a task needs evidence: the commands run and what they printed.
- **Stays out of your repo.** Tasks live in `~/.qtask`, not in git history, so notes, half-ideas and private plans never land in a commit by accident. `qtask scan` checks a commit for task IDs and qtask paths before it leaves your machine.
- **Follows your git history.** Link commits to a task, find the task of a commit, and move the links after a rebase.
- **Knows your project.** The project comes from the git remote of the folder you are in, so every clone and worktree of a repo shares one task list, and IDs get a short prefix from the repo name (`APP-14`). A repo with no `origin` remote uses its folder path.
- **Works on Windows.** Concurrent writes, Git Bash path conversion and PowerShell quoting have tests.

## Install

Python 3.11 or newer, and git 2.31 or newer.

```bash
pipx install git+https://github.com/Abubakr-Alsheikh/qtask
# or
uv tool install git+https://github.com/Abubakr-Alsheikh/qtask
```

Or run it from a clone: put `launchers/` on your PATH (`qtask` for bash and Git Bash, `qtask.cmd` for Windows), or run `python qtask.py`. Through `qtask.cmd`, cmd expands `%NAME%` and cuts text at a line break, so pass such text with `--body-file`.

Check it: `qtask --version`.

## You and your agents

Every command acts as someone. Agents pass `--agent NAME` or set `QTASK_AGENT`, one distinct name each. You are `developer`: agents send you questions with `--to developer`, and `report` lists what waits on you. In your own shell, set `QTASK_AGENT=developer`, then `qtask inbox` shows your questions and `qtask answer COMMENT_ID "text"` replies.

Every command takes `--json` for output a program can read.

### Claude Code

The skill in `skills/qtask/` tells Claude when to load qtask and how to use it: name yourself, find work, claim, comment, ask, close with evidence. Install it once:

```bash
mkdir -p ~/.claude/skills/qtask
curl -fsSL https://raw.githubusercontent.com/Abubakr-Alsheikh/qtask/main/skills/qtask/SKILL.md -o ~/.claude/skills/qtask/SKILL.md
```

### Other agents

Codex, Cursor, Aider, Gemini CLI and others read a project instructions file (`AGENTS.md`, `.cursor/rules/`, `CONVENTIONS.md`, `GEMINI.md`). Paste this into it:

```markdown
## Tasks

Track work with the `qtask` CLI. `qtask COMMAND --help` lists the flags.

- Pass `--agent NAME` on each command, or set `QTASK_AGENT`.
- Before you start: `qtask ready`, `qtask search TEXT`, then `qtask claim ID`. Exit 3 means another agent holds it.
- Record findings with `qtask comment ID "text"`.
- Ask the human with `qtask ask ID "question" --to developer`, then check `qtask inbox`.
- After each commit, run `qtask commit ID`. Before each commit, run `qtask scan --staged --message "the message"`.
- Never write a task ID or a qtask path in a commit, code, docs, a PR or an issue. Name the work itself instead.
- Close with `qtask done ID --evidence "commands you ran and what they printed"`. If you stop early, run `qtask release ID`.
```

## How it works

- **Statuses:** `backlog`, `ready`, `in_progress`, `blocked`, `needs_review`, `needs_confirm`, `done`, `dropped`. A new task starts `ready`.
- **Claims:** `claim` sets `in_progress` and a lease, 2 hours by default. Claiming again renews it. Another agent's claim fails with exit 3 until the lease runs out, and then `ready` offers the task again.
- **Questions:** `ask` moves the task to `needs_confirm`. Once every question has an answer, the task goes back to `in_progress`, or to `ready` when nobody holds it.
- **Closing:** `done` needs `--evidence`, and refuses while a question, a subtask or a checklist item is open. `drop` closes work that will not happen.
- **Blockers:** `link A --blocks B` keeps B out of `ready` until A closes.
- **Projects:** each task belongs to the project of the folder you ran the command in. `--project` picks another, and `--all` shows every project. An ID such as `APP-14` finds its task from any folder.

## Commands

| Command | Does |
|---------|------|
| `add`, `set`, `show`, `ls`, `search` | Create, change, read and find tasks |
| `ready` | Tasks an agent may claim now, by priority then age |
| `claim`, `release` | Take or give back a task. Claims expire after 2 hours unless renewed |
| `comment`, `ask`, `answer`, `resolve`, `inbox` | Notes and questions between agents and people |
| `todo` | A checklist inside a task |
| `link`, `unlink` | Blockers and related tasks |
| `commit`, `which` | Commits linked to a task, and the tasks of a commit |
| `scan` | Look for task IDs and qtask paths in what you are about to commit |
| `done`, `drop` | Close with evidence, or close without doing it |
| `tree`, `report` | Subtasks with progress, and a summary of what waits on whom |
| `project` | The project, its ID prefix and next ID. Change the prefix of new IDs |
| `plan-path`, `plans` | Dated private plan files, linked to tasks |
| `backup` | A database copy and a JSONL export, kept 7 deep |

`qtask COMMAND --help` lists the flags. The skill file has the full table and the exit codes.

### Checklists

```text
$ qtask todo APP-14 add "Redirect the old URL"
$ qtask todo APP-14 add "Update the API docs"
$ qtask todo APP-14 done 1
$ qtask todo APP-14
APP-14 checklist, 1/2 done:
  1. [x] Redirect the old URL
  2. [ ] Update the API docs
```

`undo N` reopens an item and `rm N` removes it; the other items keep their numbers. `done` refuses while an item is open, with exit 4 and the code `open_todos`.

### Commits

```text
$ qtask commit APP-14                       # link HEAD
$ qtask commit APP-14 --range main..HEAD    # link every commit of the branch
$ qtask which 3f9c2ab
3f9c2ab -> APP-14   P1 in_progress   bug: Fix the login redirect [claimed by fe-fix-1]
$ git rebase main
$ qtask commit APP-14 --rebased
Moved 1 commit link on APP-14:
  3f9c2ab -> 8d41e07 fix(auth): keep the old login URL as a redirect
```

`--rebased` matches each linked commit that left history by its subject, in the last 500 commits of HEAD. The event log keeps the old hash.

### Deploy state

```text
$ qtask set APP-14 --deployed staging --deploy-note "keep NEW_LOGIN off until QA signs off"
$ qtask ls --deployed staging
```

The states are `none`, `staging` and `production`. `report` counts them and lists what is on staging.

### Answers given in chat

When the developer answers an agent's question in chat, the agent records it with the developer as the author:

```text
$ qtask answer 7 "Yes, keep the old URL" --as developer --agent fe-fix-1
```

The question resolves as with any answer, and the event log keeps the agent's name.

### ID prefix

```text
$ qtask project set-prefix QT
my-org/my-repo now uses the prefix QT. Next ID: QT-105. Existing IDs stay as they are.
```

New tasks get the new prefix and keep the counter. Old IDs keep working.

## Keep task IDs out of the repo

Task IDs are local: nobody else can open `APP-14`, and it reads like a ticket in another tracker. Name the work in commits, code and docs, and keep the link in qtask with `qtask commit`.

`qtask scan` checks the added lines of the staged changes for the IDs of every project in your database and for qtask paths. It reports an ID in any letter case, and only when that task exists, so a ticket of another tracker with its own prefix passes. It exits 0 when it finds none, and 4 with each file and line when it does. `--message TEXT` or `--message-file PATH` checks a commit message, `--staged` adds the staged changes to that, and `--range A..B` checks the messages and changes of existing commits, such as `origin/main..HEAD` before a push.

qtask installs no hooks. To run it on every commit, add these two files to `.git/hooks/` in your repo and make them executable (`chmod +x`). They stay quiet on a clean commit and print each hit on a refused one:

```sh
#!/bin/sh
# .git/hooks/pre-commit: stop a commit that adds a task ID or a qtask path.
command -v qtask >/dev/null 2>&1 || exit 0
exec qtask scan >/dev/null
```

```sh
#!/bin/sh
# .git/hooks/commit-msg: the same check for the commit message.
command -v qtask >/dev/null 2>&1 || exit 0
exec qtask scan --message-file "$1" >/dev/null
```

Git for Windows runs hooks with its own `sh`, so the `qtask` launcher on your PATH works there too. Skip the check once with `git commit --no-verify`.

## Where data lives

`QTASK_HOME`, default `~/.qtask`: the database `qtask.db`, plan files under `plans/`, and backups under `backups/`. qtask makes no network calls. Delete the folder and it is gone. On Linux and macOS only your user can read it.

Keep `QTASK_HOME` on a local disk. SQLite's WAL mode needs every process on one machine, so a network drive breaks locking, and Dropbox, OneDrive or iCloud copy the database and its `-wal` file apart, so two machines hand out the same IDs.

qtask needs SQLite 3.24 or newer with JSON functions. The Python builds from python.org, Homebrew, uv and current Linux distributions all have it; on an older one, qtask says so and stops.

| Variable | Meaning |
|----------|---------|
| `QTASK_HOME` | Data folder, an absolute path |
| `QTASK_AGENT` | Agent name when `--agent` is absent |
| `QTASK_NO_FTS` | Use plain `LIKE` search instead of SQLite FTS5 when the database is created |

## Development

```bash
python tests/test_qtask.py
```

The tests never touch your real database: each one runs in a temp `QTASK_HOME`. CI runs them on Windows, Linux and macOS with Python 3.11 to 3.15. See [CONTRIBUTING.md](CONTRIBUTING.md) before you send a change.

What comes next: [ROADMAP.md](ROADMAP.md). Changes per version: [CHANGELOG.md](CHANGELOG.md).

## License

MIT, see [LICENSE](LICENSE).
