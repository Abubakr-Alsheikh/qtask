# Changelog

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
