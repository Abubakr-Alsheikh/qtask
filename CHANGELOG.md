# Changelog

## 0.7.0 (unreleased)

Show the right task first, and filter and keep lists by time, number and field.

- **One order for `next`, `ready`, `ls` and `search`: `important`.** Free planned work comes first, then the backlog, then parents with open parts, then held tasks (claimed, blocked, in progress, in review, parked), then closed ones. Inside each group the highest score comes first, then the oldest task. The score adds points: P0 60, P1 30, P2 10, P3 0; manager request 50, stakeholder 40; bug 25; students 12, teachers 9, parents 7, managers 5, admins 3, team -3; for each open task it blocks 5 plus a quarter of that task's own score, at most 40; overdue 40, due within 2 days 25, due within 7 days 10; 1 a week of age, at most 10.
- `next` prints the reasons with their points: `why first: P0 +60, bug +25, for students +12, planned; score 97`. `ls --explain`, `ready --explain` and `search --explain` print the same line under each row. JSON rows add `score`, `why` and `reasons`. **Change:** the `why` text now carries points and the score, and `next` says `Top ready task` without `by impact`.
- `qtask config` lists the weights; `config set NAME VALUE`, `config unset NAME` and `config reset` change them for one project. They live in the `meta` table.
- `--sort important|impact|priority|oldest|newest|updated|due|id` and `--reverse` on `ls`, `ready` and `search`. `impact` keeps the team hub's order of 0.4 to 0.6. JSON lists add `sort`. **Change:** `ls`, `ready` and `search` default to `important`; `ls` sorted by priority before, `ready` by impact, `search` by priority. `--sort priority` and `--sort impact` give the old orders.
- Time filters on `ls`, `ready` and `facets`: `--created-since`, `--created-before`, `--updated-since` take `2026-11-01` or a time ago (`7d`, `24h`, `2w`); `--stale 14` lists tasks with no activity for 14 days; `--due-before 7d` and `--overdue` use the due date.
- `--top N` is `--limit N` on every list and on `next`.
- Saved views: `view save NAME [ls|ready|search|facets|next] FLAGS`, `view NAME [FLAGS]`, `view ls`, `view rm NAME`. The `more:` line of a view repeats `qtask view NAME`.
- Due dates: `--due YYYY-MM-DD`, `+3d`, `2w` or `today` on `add` and `set` (`""` clears). `show` and `brief` print `Due: 2026-11-25 (in 5 days)`, list rows print `[due ...]` or `[overdue ...]`, and `report` has a `due` section: overdue or due within 7 days.
- `--mine` on `ls`, `ready` and `facets` uses `QTASK_SIDES` and the new `QTASK_AREAS`; without either it exits 2 with `mine_unset`. `next` reads `QTASK_AREAS` as its default `--area`, as it reads `QTASK_SIDES`.
- `--side be` also matches a parent with no side of its own whose open parts include be, and `--side none` leaves out a parent whose parts have a side. `facets` counts such a parent as `fe+be`. Task rows carry `part_sides`.
- Schema 11 adds the `due` column and the `views` table. It only adds.

## 0.6.1 (unreleased)

Organise tasks by side and area.

- A new side, `design`, as on the team hub. Schema 10 widens the side check; it only adds.
- `ls`, `ready` and `facets` filters take comma lists, as `next` does: `--side be,fe`, `--kind bug,feature`, `--pri P0,P1`, `--audience students,teachers`, `--requested-by manager,stakeholder`, `--area books,catalog`, `--status ready,backlog`. `none` matches an empty field: `--side none`, `--area none`, `--audience none`. An area cannot be named `none`. `--closed` is an alias of `--include-closed`. `ready` no longer lists `--status`, which it never used.
- `add` without `--area`, in a project with areas, prints the areas that fit, from the area names and notes and the tasks in them, with the `set` command. JSON adds `area_hint`. `area suggest TITLE|ID` prints the same list for a title or an existing task.
- `facets` counts `lock` too.
- `publish --fields side` no longer stops with a Python error for a mobile, content or qa task: every side but docs goes to the hub as it is. Without `--kind`, a feature, chore, content or research task keeps its kind on the hub instead of becoming a task.
- Every flag and argument in `--help` has a line of help.

## 0.6.0 (unreleased)

Pick the right work in one call.

- `next` skips parents that have open subtasks, so it offers a subtask, and prints `why first:` with the reasons of the impact order (`manager request, bug, for students, P3, planned`). `--side`, `--kind` and `--pri` take comma lists, `--area` includes the areas below, `--limit N` lists up to 10 candidates. `QTASK_SIDES=be,infra` is your default `--side`. `--expand` prints whole inbox comments and the brief of the top task. JSON adds `candidates` and a `why` field. **Change:** `next` can pick a different task than 0.5 did, a subtask in place of its parent.
- Task rows carry `lock` and `lock_reason`: `free`, `claimed`, `blocked`, `needs_decision`, `in_review`, `has_subtasks` or `closed`. `lock` is one of the short `--json` fields; `ready` marks a parent with `[has N open subtasks: take one]`.
- Text that another agent wrote prints between `<<<text by NAME>>>` and `<<<end>>>` in `show`, `brief`, `prompt`, `export --pack`, `next --expand` and `search --expand`. Text by the reader, the developer or qtask prints bare. Markers inside the text are defused. `prompt` tells the new session what the fences mean. JSON keeps the plain text.
- `facets [--ready]` counts tasks by lane, status, priority, kind, side, audience, requester and area, with the filters of `ls`.
- `similar` scores 0 to 100 on the title, its words found in the other task's title and body, and the area, with simple English stems (crash and crashes match). `--body-file` and `--area` add to the score. **Change:** `score` runs 0 to 100, no longer 0 to 1. `add` exits 4 with `duplicate_likely` when an open task scores 90 or more and the title has three words or more; `--duplicate-reason TEXT` adds it anyway and logs the reason, `--no-similar` skips the check. From 75 it warns.

## 0.5.0 (unreleased)

Bound the output, so an agent reads a list without filling its context.

- Lists page: `ls`, `ready`, `search`, `review`, `inbox`, `tree` (with no ID), `plans` and `decisions` print 20 rows. `--limit N` (1 to 100), `--page N`, `--offset N` and `--all-pages`. A cut list starts with `showing 21-40 of 133 (page 2 of 7)` and ends with `more: <the next command>`. `--json` adds a `page` object and a `total`. `ready` keeps its footer counts over every claimable task. `QTASK_LIMIT` sets your default; `QTASK_LIMIT=0` prints every row, as before. **Change:** `--limit` above 100 now exits 2, and `ready` drops its `... N more` line for the `more:` line.
- `--json` list rows carry 17 short fields and leave out empty ones. **Change:** a script that reads `body`, `tags`, `plan_path` or another long field from a list row now passes `--full-json` (alias `--raw-json`). `--fields id,title,status` picks fields in text and JSON; an unknown field exits 2 with `unknown_field`. `next --json` trims `next_task` the same way.
- Every JSON answer, errors too, carries `"schema": 1`. `--compact` prints JSON on one line.
- `--max-chars N` cuts each long body, comment and summary to N characters and says how much it cut.
- `search` cuts each comment hit to about 160 characters around the match and marks it `body_cut`; `--expand` prints whole comments. `search` pages tasks and comments together.
- **Change:** `show` leaves out the event log and prints the last 10 comments, with `Events: N (qtask show ID --events)`. `--events` adds the log, `--full` prints every comment, the log and full child rows. JSON gains `comment_count` and `event_count`, and has `events` only with one of the flags.
- **Change:** `report` prints 5 rows a section, with the section's count and `+N more: qtask report --section NAME`, in the order you act on them: waiting on the developer, blocked, parked without a reason, idle claims, stale claims, in progress with nobody holding them, open checklists, staging, parents. `--section NAME` prints one section whole; `--full` prints all of them. JSON lists follow the same cap and add `sections` with `total`, `shown` and `more`.
- The skill splits in two: `SKILL.md` (about 5 KB, down from 21 KB) with three rules, a start-here table, how to read output and the exit codes, and `reference.md` with the full tables.

## 0.4.0 (unreleased)

Hand work to agent sessions and see what waits on whom.

- Leases follow activity. Each event of the claim holder on the task renews the claim. Before each command, qtask releases every expired claim back to the lane it came from (the new `claimed_from`), with an `auto_released` event by `qtask`. `release` also returns the task to that lane. `report` lists claims idle for more than 24 hours.
- `ready` lists the planned lane (status `ready`), then the backlog, with a lane column and a footer counting lanes, sides and priorities. `--planned` and `--backlog` pick one lane.
- `--requested-by manager|stakeholder|developer|scan|agent`, `--requester NAME`, `--requested-on DATE` and `--audience students|teachers|parents|managers|admins|team` on `add` and `set`, and as filters on `ls` and `ready`. `--sort impact|oldest|priority`; `ready` sorts by impact by default: manager and stakeholder requests, then bugs, then audience, then priority, then age.
- `review` lists the `needs_review` queue, oldest first. `approve ID --evidence` closes a task with evidence; `reject ID --reason` moves it to `in_progress` and sends the reason to the submitter's inbox.
- `needs_confirm` and `blocked` need a reason: an open question, an open blocker, or `--waiting-on developer|manager|person|external` with `--waiting-note`. Without one, exit 4 with `waiting_on_required`. `decisions` groups open questions and parked tasks by who must act, with the exact command. `report` lists parked tasks with no reason.
- `brief ID` prints about 2 KB: status, claim, next step, summary, plan, branch, worktree, open checklist items, open questions, blockers, context files and the body. `set --next` and `set --summary` fill the new fields.
- A `handback` comment needs `Summary:`, `Checks run:` and `Left:` parts, else exit 2 with `handback_incomplete`. Its Summary becomes the task summary.
- `area add NAME --parent P` builds a tree of areas per project. `area --tree` shows open and total counts rolled up to the parents. `--area` on `ls` and `ready` includes the areas below. `report --by area|requester` adds open counts.
- `next` lists comments addressed to you, your idle and expired claims, and the top ready task, each with the commands to run.
- `prompt ID --agent NAME` prints a session prompt: the brief, where to work, the setup command (`project set-setup CMD` or `--setup`) and the rules for claims, comments, scans, commits and hand-backs. `context ID add PATH --note` keeps file links on a task. `export ID --pack [DIR]` writes all of it to `pack.md`, and refuses a folder inside a git repo.
- `split ID --part be --part fe` makes one subtask per side; `take ID --part be` claims one.
- `add` prints open tasks with a similar title, and `similar TITLE` checks before you add.
- New kinds `feature`, `chore`, `content`, `research`; new sides `mobile`, `content`, `qa`. `template KIND` prints a body template. `add --kind bug` needs `Steps:`, `Expected:` and `Actual:` lines, else exit 2 with `bug_template`.
- `claim` warns when the agent then holds more than two claims. The claim still stands.
- `report` lists tasks in `in_progress` that nobody holds, such as a rejected task. `ready` and `next` do not show them, so they could sit unseen.
- Schema 7 adds `claimed_from`, `claim_ttl_hours` and `claim_active_at`, and fills them for open claims from the event log. Schema 8 adds the request, audience, waiting, next step, summary and area columns, widens the kind, side and comment kind lists, and gives tasks tagged `manager-list*` `requested_by = manager` with the date from the tag (the tags stay). Schema 9 adds the `areas` and `context_files` tables. Nothing is dropped or renamed.

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
