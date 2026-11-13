# Changelog

## 0.3.0 (unreleased)

Send a task to a TaskFlow team hub and read back what changed there.

- `publish ID --scope TEXT | --scope-file FILE` sends a task to the hub. By default it sends the title and the scope text you pass. `--fields` adds `checklist` (open items as acceptance criteria), `priority`, `side` and `tags`. The body, comments, evidence and plan never leave.
- `publish` checks each line first for task IDs, qtask paths, private keys, API tokens, `KEY=value` secret lines, IPv4 addresses and email addresses. A hit exits 4 with `publish_blocked` and the field and line.
- `publish` shows the exact JSON and the URL, then asks in the terminal. Without a terminal it exits 4 with `confirm_required`, and no flag or variable skips the question. `--dry-run` stops after the preview.
- The hub gets a random fingerprint, `ext:<uuid>`, never the task ID. When the hub already has an open task with that fingerprint, qtask links to it. A second `publish` exits 4 with `already_published`.
- `hub-pull ID` prints the hub task's status, deploy state, git links and new events and comments, marked as written by others. In a terminal it asks to save them as one comment by `hub`. `--dry-run` only prints.
- `hub` lists the project's published tasks, and `hub set-url URL` stores the project's hub URL. `QTASK_HUB_URL` and `--hub-url` override it. The URL must use https, except for 127.0.0.1 and localhost, and qtask never follows a redirect.
- The token comes from `QTASK_HUB_TOKEN` or `~/.qhub/token`. qtask never prints or stores it.
- `show` prints the hub link of a published task.
- Schema 6 adds the `hub_links` table. It only adds.

## 0.2.0 (unreleased)

Agents link their work to git history and keep task IDs out of the repo.

- `commit ID [REV...] [--range A..B] [--note TEXT]` links commits to a task: full hash, subject, author date, branch and repo. `show` lists them and `which REV` finds the tasks of a commit.
- `commit ID --rebased` moves the links after a rebase or an amend, by subject and author date, and keeps the old hash in the event log.
- `scan` looks for task IDs and qtask paths in the staged changes, a commit message (`--message`, `--message-file`) or a range of commits (`--range`). It exits 4 with the code `task_ids_found` and each file and line. The README shows how to call it from a git hook.
- `project` shows the project, its prefix and next ID, and `project set-prefix QT` changes the prefix of new IDs. The counter carries on and old IDs keep working. A prefix another project uses now or used before is refused with `prefix_taken`.
- `todo ID add|done|undo|rm` keeps a checklist inside a task. `ls`, `show` and `report` print its progress, and `done` refuses while an item is open, with the code `open_todos`.
- `set --deployed none|staging|production --deploy-note TEXT` records where the work runs. `ls --deployed` filters by it, and `report` counts it and lists what is on staging.
- `answer --as NAME` and `comment --as NAME` record what someone said in chat with that person as the author. The event log keeps the agent that wrote it down.
- `--body-file /tmp/x.md` works in Git Bash on Windows. The launcher keeps such paths as written, so qtask now asks `cygpath` for the Windows path.
- Text and `--body-file` together exit 2 with the code `text_and_body_file` on every command, and missing text exits 2 with `text_required`. Both messages say how to fix the command.
- The skill's "qtask stays out of the repo" section also keeps qtask's notes, plans and checklists, and its name, out of repo docs, PRs and notes for other people, not only its IDs and paths.
- Schema 3 to 5 add the `commits` and `todos` tables and the `deployed` and `deploy_note` columns. They only add: no table or column that 0.1 reads changes.

## 0.1.0 (2026-11-02)

First public version.

- Tasks with kinds, priorities, sides, tags, owners and two levels of subtasks.
- Claims with expiring leases, blockers, and a `ready` list of claimable work.
- Questions between agents and the developer: `ask`, `answer`, `inbox`.
- `done` needs evidence, and refuses while questions or subtasks are open.
- Project and ID prefix from the git remote, shared by every worktree.
- Dated private plan files linked to tasks.
- Full-text search with SQLite FTS5, with a `LIKE` fallback.
- Daily database backup and JSONL export.
- `--json` on every command, and fixed exit codes.
- Launchers for bash, Git Bash and Windows `cmd`/PowerShell, with a `py` fallback and symlink support.
- `--version`.
- Runs git from an absolute PATH entry only, never from the current folder.
- Data folder, database, plans and backups readable by your user only on Linux and macOS.
- Search works past SQLite's bound-parameter limit.
- Falls back to plain search when FTS5 is missing or too old, and stops with a clear message on SQLite older than 3.24 or without JSON functions.
- Every error comes back as JSON with `--json`, including file errors and Ctrl-C (exit 130).
- Remote paths keep every group (`group/subgroup/repo` on GitLab), and Azure DevOps https and ssh remotes match.
