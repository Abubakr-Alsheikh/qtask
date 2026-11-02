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
$ qtask done APP-14 --evidence "npm test: 412 passed" --agent fe-fix-1
Done APP-14.
```

Status: **0.1, early.** It runs daily on one Windows laptop with several agents at once. Commands and output may still change before 1.0.

## Why another tracker

- **Nothing to install but Python.** No packages, no server, no account. Read the whole tool in one file before you trust it.
- **Built for agents.** Claims with leases stop two agents from taking the same task. Agents ask questions with `ask`, the developer answers, and the agent finds the answer in `inbox`. Closing a task needs evidence: the commands run and what they printed.
- **Stays out of your repo.** Tasks live in `~/.qtask`, not in git history, so notes, half-ideas and private plans never land in a commit by accident.
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
- Close with `qtask done ID --evidence "commands you ran and what they printed"`. If you stop early, run `qtask release ID`.
```

## How it works

- **Statuses:** `backlog`, `ready`, `in_progress`, `blocked`, `needs_review`, `needs_confirm`, `done`, `dropped`. A new task starts `ready`.
- **Claims:** `claim` sets `in_progress` and a lease, 2 hours by default. Claiming again renews it. Another agent's claim fails with exit 3 until the lease runs out, and then `ready` offers the task again.
- **Questions:** `ask` moves the task to `needs_confirm`. Once every question has an answer, the task goes back to `in_progress`, or to `ready` when nobody holds it.
- **Closing:** `done` needs `--evidence`, and refuses while a question or a subtask is open. `drop` closes work that will not happen.
- **Blockers:** `link A --blocks B` keeps B out of `ready` until A closes.
- **Projects:** each task belongs to the project of the folder you ran the command in. `--project` picks another, and `--all` shows every project. An ID such as `APP-14` finds its task from any folder.

## Commands

| Command | Does |
|---------|------|
| `add`, `set`, `show`, `ls`, `search` | Create, change, read and find tasks |
| `ready` | Tasks an agent may claim now, by priority then age |
| `claim`, `release` | Take or give back a task. Claims expire after 2 hours unless renewed |
| `comment`, `ask`, `answer`, `resolve`, `inbox` | Notes and questions between agents and people |
| `link`, `unlink` | Blockers and related tasks |
| `done`, `drop` | Close with evidence, or close without doing it |
| `tree`, `report` | Subtasks with progress, and a summary of what waits on whom |
| `plan-path`, `plans` | Dated private plan files, linked to tasks |
| `backup` | A database copy and a JSONL export, kept 7 deep |

`qtask COMMAND --help` lists the flags. The skill file has the full table and the exit codes.

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
