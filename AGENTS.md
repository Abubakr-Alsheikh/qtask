# Notes for coding agents working on qtask

qtask is a local task tracker for AI coding agents: one Python file, standard library only, one SQLite database in `QTASK_HOME`.

If `private/AGENTS.md` exists, read it first. It holds the owner's local setup and is never committed.

## Layout

| Path | What |
|------|------|
| `qtask.py` | The whole tool. Keep it one file |
| `tests/test_qtask.py` | All tests. Run `python tests/test_qtask.py` from the repo root |
| `skills/qtask/SKILL.md` | The skill agents load to use qtask. Update it when a command, flag, default or exit code changes |
| `launchers/` | Run qtask from a clone: `qtask` (bash, Git Bash), `qtask.cmd` (Windows) |
| `README.md`, `CHANGELOG.md`, `ROADMAP.md` | User docs, changes per version, plans |

## Rules

- Standard library only. Never add a package to `dependencies`.
- Every change ships with a test. A fix ships with the test that fails without it.
- Never point a test or a manual run at a real data folder. Set `QTASK_HOME` to a new temp folder, as the tests do.
- The database schema changes only through a new function in `MIGRATIONS`. Migrations add; they never drop or rename what an older qtask reads.
- Every error reaches the user through `QtaskError`, with a stable `code` and an exit code from the table in the module docstring. With `--json`, nothing prints a traceback.
- Bump `__version__` in `qtask.py` and `version` in `pyproject.toml` together, and add the change to `CHANGELOG.md`.
- Write docs, help text and errors in short, plain sentences in the active voice. No marketing words.
