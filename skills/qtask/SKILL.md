---
name: qtask
description: Local task tracker shared by agents and the developer. Load it when you plan work, pick up or hand off a task, give a task to a subagent, ask or answer a question between agents, report status, or record what is left, a commit, a PR, a deploy or a follow-up. Load it before you commit in a repo whose work qtask tracks, and when you see a task ID such as APP-12.
---

# qtask

`qtask` is a CLI on PATH. One local database holds the tasks of every project. The project comes from the current repo, and all worktrees of a repo share it. An ID alone finds its task from any folder.

## Procedure

1. **Name yourself.** Pass `--agent NAME` or set `QTASK_AGENT`. Use a distinct name per agent, such as `be-fix-5`. The name `unknown` cannot extend or share a claim.
2. **Find work.** Run `qtask ready`. Run `qtask search TEXT` before you add a task, so you do not add it twice. Read `qtask show ID` before you start.
3. **Claim it.** Run `qtask claim ID`. Exit 3 means another agent holds it: pick other work. Run `claim` again to extend your lease on long work (default 2 hours).
4. **Keep findings on the task.** Run `qtask comment ID "text"` as you learn things. Put the steps that are left in the checklist: `qtask todo ID add "text"`, then `qtask todo ID done N`. Record follow-ups with `qtask add` and links with `qtask set ID --pr --branch --plan`. Hand back the ID, not a long report.
5. **Link your commits.** After each commit, run `qtask commit ID` in the repo. It links HEAD; pass `REV ...` or `--range A..B` for others. After a rebase or an amend, run `qtask commit ID --rebased` so the links follow the new hashes.
6. **Scan before each commit.** Stage the changes, then run `qtask scan --staged --message "your message"`. Exit 4 lists each task ID or qtask path with its file and line. Rewrite those lines and scan again.
7. **Ask for what needs a human.** Run `qtask ask ID "question" --to developer`. The task moves to `needs_confirm`. Read answers in `qtask inbox`, and run `qtask resolve COMMENT_ID` once you have read one. When the developer answers in chat instead, record it: `qtask answer COMMENT_ID "their words" --as developer`.
8. **Record the deploy state.** Run `qtask set ID --deployed staging --deploy-note "keep the flag off until checked"`, and `--deployed production` once it is live.
9. **Close with evidence.** Run `qtask done ID --evidence "commands you ran and what they printed"`. `done` refuses while a question, a subtask or a checklist item is open. Use `qtask drop ID --reason "text"` for work that will not happen. If you stop early, run `qtask release ID` and comment on what is left.

Give a subagent the task ID and the agent name to use. It claims the task, comments there and closes it; read the result with `qtask show ID`. `qtask report` gives counts, progress, open checklists, deploy state, stale claims, blocked tasks and what waits on the developer.

Use `--force` only when the developer says so. It logs an event.

## qtask stays out of the repo

qtask is private to this machine. Tasks, IDs, comments, checklists and plan files exist only in `QTASK_HOME` (default `~/.qtask`). Nobody else can open them, and an ID such as `APP-12` reads like a ticket in another tracker, so people go looking for it.

- Never write a task ID or a qtask path in a commit message, branch name, code, code comment, test, repo doc, PR or issue.
- Keep qtask's content in qtask. Do not copy a task's notes, plan, checklist or evidence into a repo doc, the repo's plans folder, a PR, an issue or a hand-off note for other people, and do not name qtask there or ask anyone else to track work with it.
- When the developer asks for a doc or a plan in the repo, write it as plain project content: what changes and why, for whoever reads the repo. No task IDs, no qtask paths, no mention of qtask.
- Name the work itself: the feature, the file, the contract section, the commit hash. Keep the link in qtask with `qtask commit`, `qtask set --pr` or a comment.
- `qtask scan` finds both. It knows the prefixes of every project in the database, current and earlier, and reports an ID in any letter case when that task exists. Run it before each commit, and run `qtask scan --range origin/main..HEAD` before you push or open a PR.
- If qtask has no field for something you need to keep, tell the user. Do not put it in the repo instead.

When the developer copies a task to an issue tracker, record the link with `qtask set ID --gh-issue URL --visibility public`.

## Commits

`qtask commit ID` stores the full hash, subject, author date, branch and repo of each commit. Linking the same commit twice changes nothing. `qtask show ID` lists the commits, and `qtask which REV` lists the tasks a commit belongs to; REV is a hash, a short hash or a name such as `HEAD`.

`--rebased` checks each linked commit. When no local branch, tag or HEAD contains it any more, qtask looks for a commit with the same subject in the last 500 commits of HEAD, and prefers the one with the same author date. It moves the link, keeps the old hash in the event log, and prints what moved and what it could not find. Run it in the repo of the commits: links from another repo stay as they are.

## Projects and IDs

`qtask project` prints the project, its short name, its ID prefix and the next ID. `qtask project --all` lists every project. `qtask project set-prefix QT` gives new tasks the prefix `QT` and keeps the counter: after `APP-104` comes `QT-105`. Existing IDs never change. A prefix is 1 to 6 letters, and qtask refuses one that another project uses now or used before.

## Checklists and subtasks

A checklist holds the steps of one task: `qtask todo ID add "text"`, `done N`, `undo N`, `rm N`, and `qtask todo ID` lists it. Numbers stay the same after `rm`. `ls`, `show` and `report` print progress such as `[todo 2/5]`.

Split larger work with `qtask add TITLE --parent ID`, and give each subagent one subtask. Subtasks stop at two levels: `add` and `set --parent` exit 2 when the parent is itself a subtask or the task already has subtasks. See the shape with `qtask tree ID`, or `qtask tree` for all open parents. Progress such as `[2/3]` counts subtasks done over total, without dropped ones.

## Plans

Get a private plan file with `qtask plan-path TOPIC --task ID`. It prints `~/.qtask/plans/<short-project>/YYYY-MM-DD/<TASK-ID>-<topic>.md`, creates the file if missing, fills its title and `**Task:**` line from the task, and sets the task's `plan_path` when it has none. Without `--task` the file is `<topic>.md`. The short project is the repo name in lowercase, such as `app`, and a second project with the same repo name gets `app2`.

Find plans with `qtask plans`, newest first. Filter with `--day YYYY-MM-DD` or `--search TEXT`, and use `--all` for every project. It lists files under `QTASK_HOME/plans/` only.

## Exit codes

| Exit | Meaning | Codes include | What you do |
|------|---------|---------------|-------------|
| 0 | Success | | Continue |
| 1 | Database, git or unexpected error | `db_error`, `git_failed`, `os_error`, `unexpected` | Report it |
| 2 | Bad usage or invalid value | `bad_usage`, `text_required`, `text_and_body_file`, `project_required`, `not_in_repo`, `bad_revision`, `empty_range`, `bad_prefix` | Fix the command. The message names the fix |
| 3 | Another agent holds a valid claim | `claim_conflict` | Pick other work |
| 4 | A rule refused | `task_closed`, `task_blocked`, `evidence_required`, `reason_required`, `already_resolved`, `done_refused` (open questions or subtasks), `open_todos` (open checklist items only), `prefix_taken`, `task_ids_found` | Read the message and fix the cause |
| 5 | Not found | `task_not_found`, `comment_not_found`, `todo_not_found`, `commit_not_found` | Check the ID, item number or hash |
| 130 | Stopped with Ctrl-C | `interrupted` | Check the task with `show`, then run the command again |

## Commands

Global flags work on all commands: `--json`, `--project X`, `--all`, `--agent NAME`. With `--json`, stdout carries `{"ok": true, ...}` or `{"ok": false, "error": {"code", "message", "exit_code"}}`.

Commands that take text (`comment`, `ask`, `answer`, `todo add`) read it from the argument or from `--body-file FILE` (`-` for stdin). `add` and `set` take `--body` or `--body-file`. Pass one, not both: both exit 2 with `text_and_body_file`.

| Command | Does |
|---------|------|
| `add TITLE [--kind --pri --side --tag --body --body-file --plan --parent --status --owner --branch --pr]` | Create a task. Defaults: `ready`, `task`, `P2` |
| `ls [--status --kind --pri --side --tag --owner --claimed-by --parent --search --limit --include-closed --deployed]` | List tasks, without done and dropped unless asked. `--deployed STATE` lists closed tasks too |
| `ready [--kind --pri --side --tag --owner --claimed-by --parent --search --limit]` | Claimable tasks: status ready, no open blocker, no valid claim, by priority then age |
| `show ID` | Task, checklist, children, links, commits, comments, events |
| `search TEXT` | Tasks and comments |
| `claim ID [--ttl-hours 2]` | Take or extend a claim. Status becomes `in_progress` |
| `release ID [--force]` | Clear the claim. Status returns to `ready`, except `needs_review`, `blocked` and `needs_confirm` with an open question |
| `set ID [--title --body --body-file --kind --status --pri --side --owner --plan --branch --worktree --pr --gh-issue --release-tag --visibility --evidence --deployed --deploy-note --parent --add-tag --remove-tag]` | Change fields. An empty value clears an optional field. `--deployed`: none, staging, production |
| `comment ID "text" [--to AGENT --kind --as NAME --body-file]` | Add a comment. Kinds: note, question, answer, decision, review. `--as` makes NAME the author |
| `resolve COMMENT_ID` | Mark a comment resolved |
| `ask ID "question" --to AGENT [--body-file]` | Open a question. Status becomes `needs_confirm` |
| `answer COMMENT_ID "text" [--as NAME --body-file]` | Answer and resolve. With no open question left, status returns to `in_progress` if claimed, else `ready`. `--as developer` records an answer given in chat; the event log keeps your name |
| `inbox [--for AGENT]` | Open questions and comments for that agent, plus owned tasks in `needs_confirm` or `needs_review` |
| `todo ID`, `todo ID add "text" [--body-file]`, `todo ID done N...`, `todo ID undo N...`, `todo ID rm N...` | The task's checklist |
| `link ID --blocks ID2` or `--relates ID2`, `unlink` the same way | Manage links. An open blocker keeps ID2 out of `ready` and refuses its claim |
| `commit ID [REV...] [--range A..B] [--note TEXT]` | Link commits, HEAD by default |
| `commit ID --rebased` | Move links to the new hashes after a rebase or an amend |
| `which REV` | Tasks a commit is linked to |
| `scan [--staged] [--message TEXT \| --message-file PATH]`, `scan --range A..B` | Look for task IDs and qtask paths. Default: the staged changes. Exit 4 with `task_ids_found` on a hit |
| `done ID --evidence "text" [--force]` | Close. Exit 4 without evidence, or with an open question, subtask or checklist item |
| `drop ID --reason "text"` | Close without doing it |
| `report` | Status counts, parent progress, open checklists, deploy counts, staging tasks, stale claims, blocked tasks, what waits on the developer |
| `tree [ID]` | Indented parent and subtasks with status, priority and progress, or all open parents with no ID |
| `project [--all]`, `project set-prefix PREFIX` | Show the project and its next ID, or change the prefix of new IDs |
| `plan-path TOPIC [--task ID]` | Create the dated plan file and print its path. With `--task`, link it to the task |
| `plans [--day YYYY-MM-DD --search TEXT]` | Plan files, newest first: date, task and status if linked, title, path |
| `backup` | Write a database copy and a JSONL export, keeping 7 of each. Also runs on the first write of each day |

Statuses: backlog, ready, in_progress, blocked, needs_review, needs_confirm, done, dropped. Priorities: P0 to P3. Sides: fe, be, infra, docs, other. Kinds: task, bug, decision, question, improvement, review.

## Shell notes

Bash, zsh and Git Bash need nothing special. Set the agent with `export QTASK_AGENT=name`. In Git Bash, `--body-file /tmp/note.md` works.

In PowerShell, use `--body-file` for any text with double quotes, `%`, or more than one line, and `--message-file` for such a commit message:

- PowerShell 5.1 drops inner double quotes in arguments to any program, without an error: write `\"` in place of `"`.
- Through the `qtask.cmd` launcher, `cmd.exe` also expands `%NAME%` inside arguments, and cuts a multi-line argument at the first line break, dropping every flag after it, with exit 0.

Before you capture output in a variable or pipe, run `[Console]::OutputEncoding = [Text.Encoding]::UTF8`, or text outside ASCII (Arabic, Chinese, accents) comes back garbled. The stored text stays correct either way.

Set the agent with `$env:QTASK_AGENT = 'name'`.
