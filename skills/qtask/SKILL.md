---
name: qtask
description: Local task tracker shared by agents and the developer. Load it when you plan work, pick up or hand off a task, give a task to a subagent, ask or answer a question between agents, report status, or record what is left, a PR or a follow-up. Also load it when you see a task ID such as APP-12.
---

# qtask

`qtask` is a CLI on PATH. One local database holds tasks for all projects. Your project comes from the current repo, and all worktrees of a repo share it. An ID alone finds its task from any folder.

## Procedure

1. **Name yourself.** Pass `--agent NAME` or set `QTASK_AGENT`. Use a distinct name per agent, such as `be-fix-5`. The name `unknown` cannot extend or share a claim.
2. **Find work.** Run `qtask ready`, then `qtask search TEXT` to check for existing tasks before you add one. Read `qtask show ID` before you start.
3. **Claim before you start.** Run `qtask claim ID`. On exit 3 another agent holds it: pick other work. Run `claim` again to extend your lease on long work (default 2 hours). `ready` also lists expired claims you can take over.
4. **Keep findings on the task.** Run `qtask comment ID "text"` as you learn things, and return the ID instead of pasting a long report. Record links with `qtask set ID --pr --branch --plan`, and record follow-ups with `qtask add`.
5. **Ask for anything that needs a human.** Run `qtask ask ID "question" --to developer`. The task moves to `needs_confirm`. Check `qtask inbox --for NAME` for answers and `qtask resolve COMMENT_ID` once you have read one.
6. **Give subagents the ID.** Tell the subagent the task ID and the agent name to use. It claims the task, comments there and closes it. Read the result with `qtask show ID`.
7. **Close with evidence.** Run `qtask done ID --evidence "commands you ran and their results"`. Write what you ran and what it printed. Use `qtask drop ID --reason "text"` for work that will not happen. If you stop early, run `qtask release ID` and comment on what is left.
8. **Report from the data.** `qtask report` gives counts, parent progress, stale claims, blocked tasks, what waits on the developer and tasks left.

Use `--force` only when the developer says so. It logs an event.

## What stays public

Tasks and plan files stay on this machine, in `QTASK_HOME` (default `~/.qtask`). qtask uploads nothing. If you copy a task to an issue tracker, record the link with `qtask set ID --gh-issue URL --visibility public`.

## Plans

Get a private plan file with `qtask plan-path TOPIC --task ID`. It prints `~/.qtask/plans/<short-project>/YYYY-MM-DD/<TASK-ID>-<topic>.md`, creates the file if missing, fills its title and `**Task:**` line from the task, and sets the task's `plan_path` when it has none. Without `--task` the file is `<topic>.md`. The short project is the repo name in lowercase, such as `app`, and a second project with the same repo name gets `app2`.

Find plans with `qtask plans`, newest first. Filter with `--day YYYY-MM-DD` or `--search TEXT`, and use `--all` for every project. It lists files under `QTASK_HOME/plans/` only, so a plan in a repo's `plans/` folder stays out of the list even when a task points at it.

## Subtasks

Split work with `qtask add TITLE --parent ID`, and give each subagent one subtask. Subtasks stop at two levels: `add` and `set --parent` exit 2 when the parent is itself a subtask or the task already has subtasks. See the shape with `qtask tree ID`, or `qtask tree` for all open parents. `ls`, `show` and `report` print progress such as `[2/3]`: subtasks done over total, without dropped ones. A parent cannot close while a subtask is open.

## Exit codes

| Code | Meaning | What you do |
|------|---------|-------------|
| 0 | Success | Continue |
| 1 | Database or unexpected error | Report it |
| 2 | Bad usage or invalid value | Fix the command |
| 3 | Another agent holds a valid claim | Pick other work |
| 4 | A rule refused: closed, blocked, open question or child, no evidence | Read the message and fix the cause |
| 5 | Task or comment not found | Check the ID |
| 130 | Stopped with Ctrl-C | Check the task with `show`, then run the command again |

## Commands

Global flags work on all commands: `--json`, `--project X`, `--all`, `--agent NAME`. With `--json`, stdout carries `{"ok": true, ...}` or `{"ok": false, "error": {"code", "message", "exit_code"}}`.

| Command | Does |
|---------|------|
| `add TITLE [--kind --pri --side --tag --body --body-file --plan --parent --status --owner --branch --pr]` | Create a task. Defaults: `ready`, `task`, `P2` |
| `ls [--status --kind --pri --side --tag --owner --claimed-by --parent --search --limit --include-closed]` | List tasks, without done and dropped unless asked |
| `ready [same filters]` | Claimable tasks: status ready, no open blocker, no valid claim, by priority then age |
| `show ID` | Task, children, comments, links, events |
| `search TEXT` | Tasks and comments |
| `claim ID [--ttl-hours 2]` | Take or extend a claim. Status becomes `in_progress` |
| `release ID [--force]` | Clear the claim. Status returns to `ready`, except `needs_review`, `blocked` and `needs_confirm` with an open question |
| `set ID [--title --body --body-file --kind --status --pri --side --owner --plan --branch --worktree --pr --gh-issue --release-tag --visibility --evidence --parent --add-tag --remove-tag]` | Change fields. An empty value clears an optional field |
| `comment ID "text" [--to AGENT --kind --body-file]` | Add a comment. Kinds: note, question, answer, decision, review |
| `resolve COMMENT_ID` | Mark a comment resolved |
| `ask ID "question" --to AGENT` | Open a question. Status becomes `needs_confirm` |
| `answer COMMENT_ID "text"` | Answer and resolve. With no open question left, status returns to `in_progress` if claimed, else `ready` |
| `inbox [--for AGENT]` | Open questions and comments for that agent, plus owned tasks in `needs_confirm` or `needs_review` |
| `link ID --blocks ID2` or `--relates ID2`, `unlink` the same way | Manage links. An open blocker keeps ID2 out of `ready` and refuses its claim |
| `done ID --evidence "text" [--force]` | Close. Exit 4 without evidence, or with open questions or children |
| `drop ID --reason "text"` | Close without doing it |
| `report` | Status counts, parent progress, stale claims, blocked tasks, what waits on the developer |
| `tree [ID]` | Indented parent and subtasks with status, priority and progress, or all open parents with no ID |
| `plan-path TOPIC [--task ID]` | Create the dated plan file and print its path. With `--task`, link it to the task |
| `plans [--day YYYY-MM-DD --search TEXT]` | Plan files, newest first: date, task and status if linked, title, path |
| `backup` | Write a database copy and a JSONL export, keeping 7 of each. Also runs on the first write of each day |

Statuses: backlog, ready, in_progress, blocked, needs_review, needs_confirm, done, dropped. Priorities: P0 to P3. Sides: fe, be, infra, docs, other. Kinds: task, bug, decision, question, improvement, review.

## Shell notes

Bash, zsh and Git Bash need nothing special. Set the agent with `export QTASK_AGENT=name`.

In PowerShell, use `--body-file` for any text with double quotes, `%`, or more than one line:

- PowerShell 5.1 drops inner double quotes in arguments to any program, without an error: write `\"` in place of `"`.
- Through the `qtask.cmd` launcher, `cmd.exe` also expands `%NAME%` inside arguments, and cuts a multi-line argument at the first line break, dropping every flag after it, with exit 0.

Before you capture output in a variable or pipe, run `[Console]::OutputEncoding = [Text.Encoding]::UTF8`, or text outside ASCII (Arabic, Chinese, accents) comes back garbled. The stored text stays correct either way.

Set the agent with `$env:QTASK_AGENT = 'name'`.
