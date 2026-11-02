#!/usr/bin/env python3
"""qtask: local task tracker CLI for AI coding agents and the people they work with.

Python 3.11 standard library only. One SQLite database for all projects.
https://github.com/Abubakr-Alsheikh/qtask, MIT license.

Exit codes:
  0    success
  1    unexpected, file or database error
  2    bad usage or invalid value
  3    another agent holds a valid claim
  4    a rule refused the action (closed, blocked, open question or child, no evidence)
  5    task or comment not found
  130  stopped with Ctrl-C

Needs SQLite 3.24 or newer with JSON functions, and git 2.31 or newer to detect projects.

Environment:
  QTASK_HOME    data folder, an absolute path, default ~/.qtask
  QTASK_AGENT   agent name when --agent is absent, default "unknown"
  QTASK_NO_FTS  when set while the database is created, skips FTS5 and search
                uses LIKE. The tests use it to cover the fallback.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import json
import math
import os
import random
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

__version__ = "0.1.0"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_CLAIMED = 3
EXIT_REFUSED = 4
EXIT_NOT_FOUND = 5
EXIT_INTERRUPTED = 130

KINDS = ("task", "bug", "decision", "question", "improvement", "review")
STATUSES = (
    "backlog", "ready", "in_progress", "blocked",
    "needs_review", "needs_confirm", "done", "dropped",
)
CLOSED = ("done", "dropped")
PRIORITIES = ("P0", "P1", "P2", "P3")
SIDES = ("fe", "be", "infra", "docs", "other")
VISIBILITIES = ("local", "public")
COMMENT_KINDS = ("note", "question", "answer", "decision", "review")
DEVELOPER = "developer"
UNKNOWN_AGENT = "unknown"

BUSY_MS = 15000
RETRY_SECONDS = 60
MAX_TTL_HOURS = 720
MAX_TOPIC_BYTES = 120
MIN_SQLITE = (3, 24, 0)
BACKUPS_KEPT = 7
BACKUP_TABLES = ("schema_version", "meta", "projects", "tasks", "comments", "links", "events")

SQL_CLOSED = "('done','dropped')"
SQL_HAS_BLOCKER = (
    "EXISTS (SELECT 1 FROM links l JOIN tasks b ON b.id = l.from_id "
    f"WHERE l.to_id = t.id AND l.type = 'blocks' AND b.status NOT IN {SQL_CLOSED})"
)
SQL_ORDER = "t.priority, t.created_at, CAST(substr(t.id, instr(t.id, '-') + 1) AS INTEGER)"
TASK_SELECT = (
    "SELECT t.*, (SELECT json_group_array(l.from_id) FROM links l "
    "JOIN tasks b ON b.id = l.from_id "
    f"WHERE l.to_id = t.id AND l.type = 'blocks' AND b.status NOT IN {SQL_CLOSED}) "
    "AS blocked_by, "
    "(SELECT count(*) FROM tasks c WHERE c.parent_id = t.id) AS children_count, "
    "(SELECT count(*) FROM tasks c WHERE c.parent_id = t.id AND c.status = 'done') AS children_done, "
    "(SELECT count(*) FROM tasks c WHERE c.parent_id = t.id AND c.status <> 'dropped') AS children_total "
    "FROM tasks t"
)
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))}

SCHEMA_V1 = [
    """CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)""",
    """CREATE TABLE projects (
        name TEXT PRIMARY KEY COLLATE NOCASE,
        prefix TEXT NOT NULL UNIQUE,
        next_n INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
    f"""CREATE TABLE tasks (
        id TEXT PRIMARY KEY,
        project TEXT NOT NULL REFERENCES projects(name),
        parent_id TEXT REFERENCES tasks(id),
        title TEXT NOT NULL,
        body TEXT,
        kind TEXT NOT NULL DEFAULT 'task' CHECK (kind IN {KINDS!r}),
        status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN {STATUSES!r}),
        priority TEXT NOT NULL DEFAULT 'P2' CHECK (priority IN {PRIORITIES!r}),
        side TEXT CHECK (side IS NULL OR side IN {SIDES!r}),
        tags TEXT NOT NULL DEFAULT '[]',
        owner TEXT,
        claimed_by TEXT,
        claim_expires_at TEXT,
        plan_path TEXT,
        branch TEXT,
        worktree TEXT,
        pr TEXT,
        gh_issue TEXT,
        release_tag TEXT,
        visibility TEXT NOT NULL DEFAULT 'local' CHECK (visibility IN {VISIBILITIES!r}),
        evidence TEXT,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        closed_at TEXT
    )""",
    f"""CREATE TABLE comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL REFERENCES tasks(id),
        author TEXT NOT NULL,
        to_agent TEXT,
        kind TEXT NOT NULL DEFAULT 'note' CHECK (kind IN {COMMENT_KINDS!r}),
        body TEXT NOT NULL,
        resolved INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE links (
        from_id TEXT NOT NULL REFERENCES tasks(id),
        to_id TEXT NOT NULL REFERENCES tasks(id),
        type TEXT NOT NULL CHECK (type IN ('blocks','relates')),
        PRIMARY KEY (from_id, to_id, type)
    )""",
    """CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL REFERENCES tasks(id),
        actor TEXT NOT NULL,
        field TEXT NOT NULL,
        old TEXT,
        new TEXT,
        at TEXT NOT NULL
    )""",
    "CREATE INDEX idx_tasks_project_status ON tasks(project, status)",
    "CREATE INDEX idx_tasks_parent ON tasks(parent_id)",
    "CREATE INDEX idx_comments_task ON comments(task_id)",
    "CREATE INDEX idx_comments_to ON comments(to_agent, resolved)",
    "CREATE INDEX idx_links_to ON links(to_id, type)",
    "CREATE INDEX idx_events_task ON events(task_id)",
]

SCHEMA_V1_FTS = [
    """CREATE VIRTUAL TABLE search_fts USING fts5(
        task_id UNINDEXED, comment_id UNINDEXED, title, body,
        tokenize = "unicode61 remove_diacritics 2"
    )""",
    """CREATE TRIGGER tasks_fts_ai AFTER INSERT ON tasks BEGIN
        INSERT INTO search_fts(task_id, comment_id, title, body)
        VALUES (new.id, NULL, new.title, coalesce(new.body, ''));
    END""",
    """CREATE TRIGGER tasks_fts_au AFTER UPDATE OF title, body ON tasks BEGIN
        DELETE FROM search_fts WHERE task_id = old.id AND comment_id IS NULL;
        INSERT INTO search_fts(task_id, comment_id, title, body)
        VALUES (new.id, NULL, new.title, coalesce(new.body, ''));
    END""",
    """CREATE TRIGGER tasks_fts_ad AFTER DELETE ON tasks BEGIN
        DELETE FROM search_fts WHERE task_id = old.id;
    END""",
    """CREATE TRIGGER comments_fts_ai AFTER INSERT ON comments BEGIN
        INSERT INTO search_fts(task_id, comment_id, title, body)
        VALUES (new.task_id, new.id, '', new.body);
    END""",
    """CREATE TRIGGER comments_fts_au AFTER UPDATE OF body ON comments BEGIN
        DELETE FROM search_fts WHERE comment_id = old.id;
        INSERT INTO search_fts(task_id, comment_id, title, body)
        VALUES (new.task_id, new.id, '', new.body);
    END""",
    """CREATE TRIGGER comments_fts_ad AFTER DELETE ON comments BEGIN
        DELETE FROM search_fts WHERE comment_id = old.id;
    END""",
]


class QtaskError(Exception):
    """An expected failure that maps to an exit code and a stable error code."""

    def __init__(self, code, message, exit_code=EXIT_ERROR, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.extra = extra


def usage_error(message, code="bad_usage"):
    return QtaskError(code, message, EXIT_USAGE)


def refused(code, message, **extra):
    return QtaskError(code, message, EXIT_REFUSED, **extra)


# ---------------------------------------------------------------- paths, time


def msys_to_windows(path):
    """Turn a Git Bash path such as /c/Users/x into C:/Users/x on Windows."""
    if os.name == "nt":
        match = re.match(r"^/([a-zA-Z])(/.*)?$", path)
        if match:
            return f"{match.group(1).upper()}:{match.group(2) or '/'}"
    return path


def qtask_home():
    raw = os.environ.get("QTASK_HOME")
    if not raw:
        return Path.home() / ".qtask"
    home = Path(msys_to_windows(raw)).expanduser()
    if not home.is_absolute():
        raise usage_error(f"QTASK_HOME must be an absolute path, not {raw!r}.", "bad_home")
    return home


def utc_now():
    return datetime.now(timezone.utc)


def iso(moment):
    """Fixed-width UTC timestamp, so string comparison orders correctly."""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def now():
    return iso(utc_now())


# ---------------------------------------------------------- project detection


@functools.lru_cache(maxsize=1)
def git_path():
    """
    Full path of git from the absolute PATH entries, or None. Never a bare "git": on Windows that
    runs a git.exe in the current folder first, so a cloned repo could plant one.
    """
    name = "git.exe" if os.name == "nt" else "git"
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        folder = folder.strip().strip('"')
        if not folder or not os.path.isabs(folder):
            continue
        candidate = os.path.join(folder, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def run_git(*git_args):
    git = git_path()
    if git is None:
        return None
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR")}
    try:
        result = subprocess.run(
            [git, *git_args], capture_output=True, encoding="utf-8",
            errors="replace", timeout=15, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def normalize_remote(url):
    """
    Reduce a git remote URL to its path on the host: owner/repo on GitHub, group/subgroup/repo on
    GitLab. A local path or file:// remote keeps its last two folders.
    """
    url = url.strip()
    match = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*)://(?:[^@/]+@)?([^/]*)/(.*)$", url)
    if match:
        whole = match.group(1).lower() != "file"
        host, path = match.group(2).lower(), match.group(3)
    else:
        scp = re.match(r"^(?:[^@/\\]+@)?([^:/\\]+):(?![/\\])(.*)$", url)
        whole = bool(scp)
        host, path = (scp.group(1).lower(), scp.group(2)) if scp else ("", url)
    parts = [p for p in re.split(r"[/\\]+", path) if p]
    if "azure.com" in host or "visualstudio.com" in host:
        # https://dev.azure.com/org/proj/_git/repo and git@ssh.dev.azure.com:v3/org/proj/repo
        parts = [p for p in parts if p != "_git"]
        if parts and parts[0] == "v3" and host.startswith("ssh."):
            parts = parts[1:]
    if not parts:
        return None
    parts[-1] = re.sub(r"\.git$", "", parts[-1], flags=re.IGNORECASE)
    return "/".join(parts if whole else parts[-2:])


@functools.lru_cache(maxsize=1)
def detect_project():
    """Project of the current folder, or None outside a repo."""
    common = run_git("rev-parse", "--path-format=absolute", "--git-common-dir")
    if common is None:
        return None
    if "\n" in common or common.startswith("--"):
        # git before 2.31 echoes the flag it does not know and still exits 0.
        raise usage_error("qtask needs git 2.31 or newer. Pass --project OWNER/REPO.", "git_too_old")
    url = run_git("remote", "get-url", "origin")
    if url:
        slug = normalize_remote(url)
        if slug:
            return slug
    path = Path(common)
    if path.name.lower() == ".git":
        path = path.parent
    return os.path.realpath(path).replace("\\", "/")


def prefix_candidates(project):
    repo = project.rstrip("/").split("/")[-1]
    base = re.sub(r"[^A-Za-z]", "", repo)[:3].upper() or "PRJ"
    yield base
    n = 2
    while True:
        yield f"{base}{n}"
        n += 1


def short_name_candidates(project):
    repo = re.split(r"[/\\]+", project.rstrip("/\\"))[-1]
    base = re.sub(r"[^\w.\-]", "_", repo).strip(".").lower() or "project"
    if base in WINDOWS_RESERVED:
        base += "_"
    yield base
    n = 2
    while True:
        yield f"{base}{n}"
        n += 1


def free_short_name(conn, project):
    taken = {r[0] for r in conn.execute("SELECT short_name FROM projects WHERE short_name IS NOT NULL")}
    return next(name for name in short_name_candidates(project) if name not in taken)


# ------------------------------------------------------------------- database


def transient_db_error(exc):
    text = str(exc).lower()
    return any(s in text for s in ("locked", "busy", "locking protocol"))


def migrate_1(conn):
    for statement in SCHEMA_V1:
        conn.execute(statement)
    has_fts = False
    if not os.environ.get("QTASK_NO_FTS"):
        try:
            conn.execute(SCHEMA_V1_FTS[0])
            has_fts = True
        except sqlite3.OperationalError as exc:
            # No FTS5, or an FTS5 too old for the tokenizer options (before SQLite 3.27): use LIKE.
            if transient_db_error(exc):
                raise
    if has_fts:
        for statement in SCHEMA_V1_FTS[1:]:
            conn.execute(statement)
    conn.execute("INSERT INTO meta(key, value) VALUES ('fts', ?)", ("1" if has_fts else "0",))


def migrate_2(conn):
    """Give each project a short folder name for its plans. Older projects win a shared name."""
    conn.execute("ALTER TABLE projects ADD COLUMN short_name TEXT")
    for row in conn.execute("SELECT name FROM projects ORDER BY created_at, name").fetchall():
        conn.execute(
            "UPDATE projects SET short_name = ? WHERE name = ?", (free_short_name(conn, row[0]), row[0])
        )
    conn.execute("CREATE UNIQUE INDEX idx_projects_short_name ON projects(short_name)")


MIGRATIONS = {1: migrate_1, 2: migrate_2}


def schema_version(conn):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if not row:
        return 0
    row = conn.execute("SELECT max(version) FROM schema_version").fetchone()
    return row[0] or 0


def migrate(conn):
    target = max(MIGRATIONS)
    current = schema_version(conn)
    if current == target:
        return
    if current > target:
        raise QtaskError(
            "schema_too_new",
            f"The database is at schema {current} and this qtask knows {target}. Update qtask.py.",
        )
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        current = schema_version(conn)
        for version in range(current + 1, target + 1):
            MIGRATIONS[version](conn)
            conn.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)", (version, now())
            )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def check_sqlite(conn):
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        raise QtaskError(
            "sqlite_too_old",
            f"qtask needs SQLite {'.'.join(map(str, MIN_SQLITE))} or newer. "
            f"This Python uses SQLite {sqlite3.sqlite_version}.",
        )
    try:
        conn.execute("SELECT json_valid('[]')")
    except sqlite3.OperationalError as exc:
        raise QtaskError(
            "sqlite_no_json", f"qtask needs SQLite with JSON functions. This Python's SQLite has none: {exc}"
        ) from exc


def open_db():
    home = qtask_home()
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    conn = sqlite3.connect(home / "qtask.db", timeout=BUSY_MS / 1000, isolation_level=None)
    try:
        check_sqlite(conn)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {BUSY_MS}")
        conn.execute("PRAGMA foreign_keys = ON")
        if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


class Ctx:
    def __init__(self, conn, args):
        self.conn = conn
        self.args = args
        self.agent = args.agent
        self.backup_due = False
        self.committed = False

    def project(self):
        """Project from --project or cwd. Raises outside a repo."""
        if self.args.project:
            return canonical_project(self.conn, self.args.project)
        detected = detect_project()
        if detected:
            return canonical_project(self.conn, detected)
        raise usage_error(f"{no_repo_reason()} Pass --project OWNER/REPO.", "project_required")

    def scope(self):
        """Like project(), but returns None when --all asks for every project."""
        if self.args.all and self.args.project:
            raise usage_error("Pass --all or --project, not both.")
        if self.args.all:
            return None
        if not self.args.project and not detect_project():
            raise usage_error(f"{no_repo_reason()} Pass --project OWNER/REPO or --all.", "project_required")
        return self.project()


def no_repo_reason():
    if git_path() is None:
        return "git was not found on PATH, so qtask cannot tell the project."
    return "This folder is not in a git repo."


def canonical_project(conn, name):
    name = name.strip().rstrip("/")
    row = conn.execute("SELECT name FROM projects WHERE name = ?", (name,)).fetchone()
    return row["name"] if row else name


@contextlib.contextmanager
def write_tx(ctx):
    """BEGIN IMMEDIATE transaction. Marks the first write of each UTC day for a backup."""
    conn = ctx.conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        today = utc_now().strftime("%Y-%m-%d")
        row = conn.execute("SELECT value FROM meta WHERE key = 'last_backup_day'").fetchone()
        if not row or row["value"] != today:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('last_backup_day', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (today,),
            )
            ctx.backup_due = True
        yield conn
        conn.execute("COMMIT")
        ctx.committed = True
    except BaseException:
        ctx.backup_due = False
        if conn.in_transaction:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
        raise


def log_event(ctx, task_id, field, old, new, at=None):
    ctx.conn.execute(
        "INSERT INTO events(task_id, actor, field, old, new, at) VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, ctx.agent, field, old, new, at or now()),
    )


def apply_changes(ctx, row, changes):
    """Update a task's changed columns and log one event per column."""
    diffs = {k: v for k, v in changes.items() if row[k] != v}
    if not diffs:
        return False
    at = now()
    columns = ", ".join(f"{k} = ?" for k in diffs)
    ctx.conn.execute(
        f"UPDATE tasks SET {columns}, updated_at = ? WHERE id = ?",
        (*diffs.values(), at, row["id"]),
    )
    for column, value in diffs.items():
        log_event(ctx, row["id"], column, row[column], value, at)
    return True


def ensure_project(conn, project):
    """Return the project row, creating it inside the caller's write transaction."""
    row = conn.execute("SELECT * FROM projects WHERE name = ?", (project,)).fetchone()
    if row:
        return row
    taken = {r["prefix"] for r in conn.execute("SELECT prefix FROM projects")}
    prefix = next(p for p in prefix_candidates(project) if p not in taken)
    conn.execute(
        "INSERT INTO projects(name, prefix, next_n, created_at, short_name) VALUES (?, ?, 1, ?, ?)",
        (project, prefix, now(), free_short_name(conn, project)),
    )
    return conn.execute("SELECT * FROM projects WHERE name = ?", (project,)).fetchone()


def touch(ctx, task_id):
    ctx.conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now(), task_id))


def normalize_id(raw):
    return str(raw).strip().upper()


def get_row(conn, task_id):
    wanted = normalize_id(task_id)
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (wanted,)).fetchone()
    if not row:
        raise QtaskError("task_not_found", f"No task with ID {wanted}.", EXIT_NOT_FOUND)
    return row


def get_comment_row(conn, comment_id):
    try:
        number = int(comment_id)
    except ValueError:
        raise usage_error(f"A comment ID is a number, got {comment_id!r}.") from None
    row = conn.execute("SELECT * FROM comments WHERE id = ?", (number,)).fetchone()
    if not row:
        raise QtaskError("comment_not_found", f"No comment with ID {number}.", EXIT_NOT_FOUND)
    return row


def claim_is_valid(row, at=None):
    return bool(
        row["claimed_by"] and row["claim_expires_at"] and row["claim_expires_at"] > (at or now())
    )


def task_dict(row):
    data = {k: row[k] for k in row.keys()}
    data["tags"] = json.loads(data["tags"])
    data["blocked_by"] = sorted(json.loads(data["blocked_by"])) if "blocked_by" in data else []
    data["claim_valid"] = claim_is_valid(row)
    data["progress"] = (
        f"{data['children_done']}/{data['children_total']}" if data.get("children_count") else None
    )
    return data


def fetch_task(conn, task_id):
    row = conn.execute(f"{TASK_SELECT} WHERE t.id = ?", (normalize_id(task_id),)).fetchone()
    return task_dict(row)


def comment_dict(row):
    data = {k: row[k] for k in row.keys()}
    data["resolved"] = bool(data["resolved"])
    return data


def open_question_ids(conn, task_id):
    rows = conn.execute(
        "SELECT id FROM comments WHERE task_id = ? AND kind = 'question' AND resolved = 0 ORDER BY id",
        (task_id,),
    ).fetchall()
    return [r["id"] for r in rows]


def settle_needs_confirm(ctx, task_id):
    """Move a needs_confirm task on once its last open question closes."""
    row = get_row(ctx.conn, task_id)
    if row["status"] != "needs_confirm" or open_question_ids(ctx.conn, task_id):
        return
    apply_changes(ctx, row, {"status": "in_progress" if claim_is_valid(row) else "ready"})


# ----------------------------------------------------------------- validation


def choice(value, allowed, flag, upper=False):
    value = value.strip()
    value = value.upper() if upper else value.lower()
    if value not in allowed:
        raise usage_error(f"{flag} must be one of {', '.join(allowed)}. Got {value!r}.")
    return value


def clean_tags(values):
    tags = []
    for value in values or []:
        tag = value.strip()
        if not tag:
            raise usage_error("A tag cannot be empty.")
        if tag not in tags:
            tags.append(tag)
    return tags


def open_status(value, flag="--status"):
    status = choice(value, STATUSES, flag)
    if status in CLOSED:
        raise usage_error(
            f"Use 'qtask done' or 'qtask drop' to close a task, not {flag} {status}.", "use_close_command"
        )
    return status


def read_text_file(path):
    """Read UTF-8 text from a file, or from stdin when the path is '-'."""
    if path == "-":
        raw = sys.stdin.buffer.read()
    else:
        target = Path(path)
        if not target.exists():
            target = Path(msys_to_windows(path))
        try:
            raw = target.read_bytes()
        except OSError as exc:
            raise usage_error(f"Cannot read {path}: {exc}") from None
    try:
        return raw.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError:
        raise usage_error(f"{path} is not UTF-8.") from None


def load_inputs(args):
    """Read file and stdin inputs once, before any database retry."""
    body_file = getattr(args, "body_file", None)
    if hasattr(args, "text"):
        if body_file is not None:
            if args.text is not None:
                raise usage_error("Pass the text or --body-file, not both.")
            args.text = read_text_file(body_file)
        if args.text is None or not args.text.strip():
            raise usage_error("The text cannot be empty.")
    elif body_file is not None:
        if args.body is not None:
            raise usage_error("Pass --body or --body-file, not both.")
        args.body = read_text_file(body_file)


def assert_parent_allowed(conn, parent_row, task_id=None):
    """Keep subtasks to two levels: parent and child."""
    parent_id = parent_row["id"]
    if parent_id == task_id:
        raise usage_error(f"{task_id} cannot be its own parent.", "parent_cycle")
    if parent_row["parent_id"]:
        raise usage_error(
            f"{parent_id} is a subtask of {parent_row['parent_id']}. Subtasks stop at two levels.",
            "parent_too_deep",
        )
    if task_id:
        child = conn.execute("SELECT id FROM tasks WHERE parent_id = ? LIMIT 1", (task_id,)).fetchone()
        if child:
            raise usage_error(
                f"{task_id} has subtasks such as {child['id']}. Subtasks stop at two levels.",
                "parent_too_deep",
            )


# ------------------------------------------------------------------ filters


def add_scope(where, params, project, column="t.project"):
    if project is not None:
        where.append(f"{column} = ?")
        params.append(project)


def build_filters(ctx, where, params):
    a = ctx.args
    add_scope(where, params, ctx.scope())
    if a.kind:
        where.append("t.kind = ?")
        params.append(choice(a.kind, KINDS, "--kind"))
    if a.pri:
        where.append("t.priority = ?")
        params.append(choice(a.pri, PRIORITIES, "--pri", upper=True))
    if a.side:
        where.append("t.side = ?")
        params.append(choice(a.side, SIDES, "--side"))
    for tag in clean_tags(a.tag):
        where.append("EXISTS (SELECT 1 FROM json_each(t.tags) WHERE value = ?)")
        params.append(tag)
    if a.owner:
        where.append("t.owner = ?")
        params.append(a.owner)
    if a.claimed_by:
        where.append("t.claimed_by = ?")
        params.append(a.claimed_by)
    if a.parent:
        where.append("t.parent_id = ?")
        params.append(normalize_id(a.parent))
    if a.search:
        task_ids, comment_ids = search_hits(ctx.conn, a.search)
        tasks = hit_table(ctx.conn, "hit_tasks", task_ids)
        comments = hit_table(ctx.conn, "hit_comments", comment_ids)
        where.append(f"(t.id IN {tasks} OR t.id IN (SELECT task_id FROM comments WHERE id IN {comments}))")


def hit_table(conn, name, ids):
    """
    Put search hits in a temp table and return a subquery over it. An IN list of bound values
    fails past SQLite's parameter limit: 999 before SQLite 3.32, 32,766 after.
    """
    conn.execute(f"CREATE TEMP TABLE IF NOT EXISTS {name}(id PRIMARY KEY) WITHOUT ROWID")
    conn.execute(f"DELETE FROM temp.{name}")
    conn.executemany(f"INSERT OR IGNORE INTO temp.{name}(id) VALUES (?)", ((i,) for i in ids))
    return f"(SELECT id FROM temp.{name})"


def select_tasks(ctx, where, params):
    sql = f"{TASK_SELECT} WHERE {' AND '.join(where) if where else '1'} ORDER BY {SQL_ORDER}"
    limit = getattr(ctx.args, "limit", None)
    if limit is not None:
        if limit < 1:
            raise usage_error("--limit must be 1 or more.")
        sql += " LIMIT ?"
        params = [*params, limit]
    return [task_dict(r) for r in ctx.conn.execute(sql, params).fetchall()]


def task_line(task):
    side = f" {task['side']}" if task["side"] else ""
    parts = [f"{task['id']:<8} {task['priority']} {task['status']:<13} {task['kind']}{side}: {task['title']}"]
    if task["progress"]:
        parts.append(f"[{task['progress']}]")
    if task["claim_valid"]:
        parts.append(f"[claimed by {task['claimed_by']}]")
    elif task["claimed_by"]:
        parts.append(f"[claim of {task['claimed_by']} expired]")
    if task["blocked_by"]:
        parts.append(f"[blocked by {', '.join(task['blocked_by'])}]")
    return " ".join(parts)


def tasks_text(tasks, empty="No tasks."):
    return "\n".join(task_line(t) for t in tasks) if tasks else empty


def comment_line(comment):
    target = f" to {comment['to_agent']}" if comment["to_agent"] else ""
    state = "resolved" if comment["resolved"] else "open"
    return (
        f"#{comment['id']} on {comment['task_id']} [{comment['kind']}, {state}] "
        f"{comment['author']}{target}: {comment['body']}"
    )


# ------------------------------------------------------------------- search


def fts_query(text):
    tokens = [t for t in text.split() if t]
    return " ".join('"' + t.replace('"', '""') + '"*' for t in tokens)


def search_hits(conn, text):
    """Return (task IDs, comment IDs) whose text matches, FTS5 first, then LIKE."""
    rows = []
    fts = conn.execute("SELECT value FROM meta WHERE key = 'fts'").fetchone()
    query = fts_query(text)
    if fts and fts["value"] == "1" and query:
        try:
            rows = conn.execute(
                "SELECT task_id, comment_id FROM search_fts WHERE search_fts MATCH ?", (query,)
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if transient_db_error(exc):
                raise
    if rows:
        tasks = [r["task_id"] for r in rows if r["comment_id"] is None]
        comments = [int(r["comment_id"]) for r in rows if r["comment_id"] is not None]
        return tasks, comments
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    tasks = conn.execute(
        "SELECT id FROM tasks WHERE title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\'",
        (pattern, pattern),
    ).fetchall()
    comments = conn.execute(
        "SELECT id FROM comments WHERE body LIKE ? ESCAPE '\\'", (pattern,)
    ).fetchall()
    return [r["id"] for r in tasks], [r["id"] for r in comments]


# ------------------------------------------------------------------- backups


def rotate(folder, pattern):
    files = sorted(folder.glob(pattern))
    for old in files[:-BACKUPS_KEPT]:
        with contextlib.suppress(OSError):
            old.unlink()


def run_backup(conn):
    folder = qtask_home() / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    for stale in folder.glob("qtask-*.db.tmp"):
        # Left by a backup that was stopped. One process backs up per day, so none is running.
        with contextlib.suppress(OSError):
            stale.unlink()
    stamp = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    db_path = folder / f"qtask-{stamp}.db"
    jsonl_path = folder / f"qtask-{stamp}.jsonl"
    temp_path = folder / f"qtask-{stamp}.db.tmp"
    dest = sqlite3.connect(temp_path)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    os.replace(temp_path, db_path)
    with open(jsonl_path, "w", encoding="utf-8", newline="\n") as handle:
        for table in BACKUP_TABLES:
            for row in conn.execute(f"SELECT * FROM {table}"):
                record = {"table": table, "row": {k: row[k] for k in row.keys()}}
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    rotate(folder, "qtask-*.db")
    rotate(folder, "qtask-*.jsonl")
    return db_path, jsonl_path


# ------------------------------------------------------------------ commands


def cmd_add(ctx):
    a = ctx.args
    if a.all:
        raise usage_error("add needs one project. Pass --project, not --all.")
    title = a.title.strip()
    if not title:
        raise usage_error("The title cannot be empty.")
    kind = choice(a.kind, KINDS, "--kind")
    priority = choice(a.pri, PRIORITIES, "--pri", upper=True)
    status = open_status(a.status)
    side = choice(a.side, SIDES, "--side") if a.side else None
    tags = clean_tags(a.tag)
    parent_id = normalize_id(a.parent) if a.parent else None
    if parent_id and not a.project:
        project = get_row(ctx.conn, parent_id)["project"]
    else:
        project = ctx.project()
    with write_tx(ctx) as conn:
        if parent_id:
            parent = get_row(conn, parent_id)
            if parent["project"] != project:
                raise usage_error(f"Parent {parent_id} belongs to another project.")
            assert_parent_allowed(conn, parent)
        row = ensure_project(conn, project)
        task_id = f"{row['prefix']}-{row['next_n']}"
        conn.execute("UPDATE projects SET next_n = next_n + 1 WHERE name = ?", (project,))
        at = now()
        conn.execute(
            "INSERT INTO tasks(id, project, parent_id, title, body, kind, status, priority, side, "
            "tags, owner, plan_path, branch, pr, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id, row["name"], parent_id, title, a.body, kind, status, priority, side,
                json.dumps(tags, ensure_ascii=False), a.owner, a.plan, a.branch, a.pr,
                ctx.agent, at, at,
            ),
        )
        log_event(ctx, task_id, "created", None, title, at)
    task = fetch_task(ctx.conn, task_id)
    return {"task": task}, f"Created {task_id}: {title}"


def cmd_ls(ctx):
    a = ctx.args
    where, params = [], []
    build_filters(ctx, where, params)
    if a.status:
        statuses = [choice(s, STATUSES, "--status") for s in a.status]
        where.append(f"t.status IN ({','.join('?' * len(statuses))})")
        params.extend(statuses)
    elif not a.include_closed:
        where.append(f"t.status NOT IN {SQL_CLOSED}")
    tasks = select_tasks(ctx, where, params)
    return {"tasks": tasks, "count": len(tasks)}, tasks_text(tasks)


def stale_claims(ctx, project):
    where = ["t.claimed_by IS NOT NULL", "t.claim_expires_at <= ?", f"t.status NOT IN {SQL_CLOSED}"]
    params = [now()]
    add_scope(where, params, project)
    sql = f"{TASK_SELECT} WHERE {' AND '.join(where)} ORDER BY {SQL_ORDER}"
    return [task_dict(r) for r in ctx.conn.execute(sql, params).fetchall()]


def cmd_ready(ctx):
    at = now()
    where = [
        "t.status = 'ready'",
        f"NOT {SQL_HAS_BLOCKER}",
        "(t.claimed_by IS NULL OR t.claim_expires_at IS NULL OR t.claim_expires_at <= ?)",
    ]
    params = [at]
    build_filters(ctx, where, params)
    tasks = select_tasks(ctx, where, params)
    stale = [t["id"] for t in stale_claims(ctx, ctx.scope())]
    text = tasks_text(tasks, "No ready tasks.")
    if stale:
        text += f"\nExpired claims you can take over with claim: {', '.join(stale)}"
    return {"tasks": tasks, "count": len(tasks), "stale_claims": stale}, text


def cmd_show(ctx):
    conn = ctx.conn
    row = get_row(conn, ctx.args.id)
    task_id = row["id"]
    task = fetch_task(conn, task_id)
    children = [
        task_dict(r)
        for r in conn.execute(
            f"{TASK_SELECT} WHERE t.parent_id = ? ORDER BY {SQL_ORDER}", (task_id,)
        )
    ]
    comments = [
        comment_dict(r)
        for r in conn.execute("SELECT * FROM comments WHERE task_id = ? ORDER BY id", (task_id,))
    ]
    events = [
        {k: r[k] for k in r.keys()}
        for r in conn.execute("SELECT * FROM events WHERE task_id = ? ORDER BY id", (task_id,))
    ]
    links = {"blocks": [], "blocked_by": [], "relates": []}
    for r in conn.execute(
        "SELECT from_id, to_id, type FROM links WHERE from_id = ? OR to_id = ? ORDER BY from_id, to_id",
        (task_id, task_id),
    ):
        if r["type"] == "blocks":
            key, other = ("blocks", r["to_id"]) if r["from_id"] == task_id else ("blocked_by", r["from_id"])
        else:
            key, other = "relates", (r["to_id"] if r["from_id"] == task_id else r["from_id"])
        links[key].append(other)
    lines = [task_line(task), f"Project: {task['project']}"]
    if task["progress"]:
        lines.append(f"Progress: {task['progress']} subtasks done")
    for label, key in (
        ("Parent", "parent_id"), ("Tags", "tags"), ("Owner", "owner"), ("Claim until", "claim_expires_at"),
        ("Plan", "plan_path"), ("Branch", "branch"), ("Worktree", "worktree"), ("PR", "pr"),
        ("GitHub issue", "gh_issue"), ("Release", "release_tag"), ("Evidence", "evidence"),
        ("Closed", "closed_at"),
    ):
        if task[key]:
            value = ", ".join(task[key]) if key == "tags" else task[key]
            lines.append(f"{label}: {value}")
    lines.append(f"Visibility: {task['visibility']}")
    lines.append(f"Created: {task['created_at']} by {task['created_by']}")
    if task["body"]:
        lines += ["", task["body"]]
    if children:
        lines += ["", "Children:"] + [f"  {task_line(c)}" for c in children]
    if any(links.values()):
        lines += ["", "Links:"] + [f"  {k}: {', '.join(v)}" for k, v in links.items() if v]
    if comments:
        lines += ["", "Comments:"] + [f"  {comment_line(c)}" for c in comments]
    lines += ["", "Events:"]
    lines += [f"  {e['at']} {e['actor']} {e['field']}: {e['old']} -> {e['new']}" for e in events]
    data = {"task": task, "children": children, "comments": comments, "links": links, "events": events}
    return data, "\n".join(lines)


def cmd_search(ctx):
    conn = ctx.conn
    text = ctx.args.text.strip()
    if not text:
        raise usage_error("The search text cannot be empty.")
    project = ctx.scope()
    task_ids, comment_ids = search_hits(conn, text)
    tasks, comments = [], []
    if task_ids:
        where = [f"t.id IN {hit_table(conn, 'hit_tasks', task_ids)}"]
        params = []
        add_scope(where, params, project)
        rows = conn.execute(
            f"{TASK_SELECT} WHERE {' AND '.join(where)} ORDER BY {SQL_ORDER}", params
        ).fetchall()
        tasks = [task_dict(r) for r in rows]
    if comment_ids:
        where = [f"c.id IN {hit_table(conn, 'hit_comments', comment_ids)}"]
        params = []
        add_scope(where, params, project)
        rows = conn.execute(
            "SELECT c.*, t.project, t.title AS task_title FROM comments c "
            f"JOIN tasks t ON t.id = c.task_id WHERE {' AND '.join(where)} ORDER BY c.id",
            params,
        ).fetchall()
        comments = [comment_dict(r) for r in rows]
    lines = [task_line(t) for t in tasks] + [comment_line(c) for c in comments]
    return (
        {"query": text, "tasks": tasks, "comments": comments},
        "\n".join(lines) if lines else "No matches.",
    )


def cmd_claim(ctx):
    a = ctx.args
    if not math.isfinite(a.ttl_hours) or not 0 < a.ttl_hours <= MAX_TTL_HOURS:
        raise usage_error(f"--ttl-hours must be above 0 and at most {MAX_TTL_HOURS}.")
    task_id = normalize_id(a.id)
    moment = utc_now()
    at = iso(moment)
    expires = iso(moment + timedelta(hours=a.ttl_hours))
    me = None if ctx.agent == UNKNOWN_AGENT else ctx.agent
    with write_tx(ctx) as conn:
        old = get_row(conn, task_id)
        waiting = old["status"] == "needs_confirm" and open_question_ids(conn, task_id)
        new_status = "needs_confirm" if waiting else "in_progress"
        updated = conn.execute(
            f"""UPDATE tasks AS t
                SET claimed_by = ?, claim_expires_at = ?, status = ?, updated_at = ?
                WHERE t.id = ?
                  AND t.status NOT IN {SQL_CLOSED}
                  AND t.status <> 'blocked'
                  AND NOT {SQL_HAS_BLOCKER}
                  AND (t.claimed_by IS NULL OR t.claimed_by = ?
                       OR t.claim_expires_at IS NULL OR t.claim_expires_at <= ?)""",
            (ctx.agent, expires, new_status, at, task_id, me, at),
        ).rowcount
        if updated == 0:
            if old["status"] in CLOSED:
                raise refused("task_closed", f"{task_id} is {old['status']}.", status=old["status"])
            blockers = fetch_task(conn, task_id)["blocked_by"]
            if old["status"] == "blocked" or blockers:
                why = f"blocked by {', '.join(blockers)}" if blockers else "in status blocked"
                raise refused("task_blocked", f"{task_id} is {why}.", blocked_by=blockers)
            raise QtaskError(
                "claim_conflict",
                f"{task_id} is claimed by {old['claimed_by']} until {old['claim_expires_at']}.",
                EXIT_CLAIMED,
                claimed_by=old["claimed_by"],
                claim_expires_at=old["claim_expires_at"],
            )
        if old["claimed_by"] and old["claimed_by"] != ctx.agent:
            log_event(ctx, task_id, "claim_takeover", old["claimed_by"], ctx.agent, at)
        elif old["claimed_by"] != ctx.agent:
            log_event(ctx, task_id, "claimed_by", None, ctx.agent, at)
        log_event(ctx, task_id, "claim_expires_at", old["claim_expires_at"], expires, at)
        if old["status"] != new_status:
            log_event(ctx, task_id, "status", old["status"], new_status, at)
    task = fetch_task(ctx.conn, task_id)
    return {"task": task}, f"Claimed {task_id} as {ctx.agent} until {expires}. Status: {new_status}."


def cmd_release(ctx):
    a = ctx.args
    with write_tx(ctx) as conn:
        row = get_row(conn, a.id)
        task_id = row["id"]
        if row["status"] in CLOSED:
            raise refused("task_closed", f"{task_id} is {row['status']}.", status=row["status"])
        mine = row["claimed_by"] == ctx.agent and ctx.agent != UNKNOWN_AGENT
        if claim_is_valid(row) and not mine and not a.force:
            raise QtaskError(
                "claim_conflict",
                f"{task_id} is claimed by {row['claimed_by']} until {row['claim_expires_at']}. "
                "Pass --force to release it anyway.",
                EXIT_CLAIMED,
                claimed_by=row["claimed_by"],
                claim_expires_at=row["claim_expires_at"],
            )
        if claim_is_valid(row) and not mine:
            log_event(ctx, task_id, "force", None, f"release --force, held by {row['claimed_by']}")
        keep = row["status"] in ("needs_review", "blocked") or (
            row["status"] == "needs_confirm" and open_question_ids(conn, task_id)
        )
        apply_changes(
            ctx, row,
            {"claimed_by": None, "claim_expires_at": None, "status": row["status"] if keep else "ready"},
        )
    task = fetch_task(ctx.conn, task_id)
    return {"task": task}, f"Released {task_id}. Status: {task['status']}."


def text_or_null(value):
    if value is None:
        return None
    value = value.strip()
    return value or None


def cmd_set(ctx):
    a = ctx.args
    changes = {}
    if a.title is not None:
        if not a.title.strip():
            raise usage_error("The title cannot be empty.")
        changes["title"] = a.title.strip()
    if a.body is not None:
        changes["body"] = a.body or None
    if a.kind is not None:
        changes["kind"] = choice(a.kind, KINDS, "--kind")
    if a.pri is not None:
        changes["priority"] = choice(a.pri, PRIORITIES, "--pri", upper=True)
    if a.status is not None:
        changes["status"] = open_status(a.status)
    if a.side is not None:
        changes["side"] = choice(a.side, SIDES, "--side") if a.side.strip() else None
    if a.visibility is not None:
        changes["visibility"] = choice(a.visibility, VISIBILITIES, "--visibility")
    for attr, column in (
        ("owner", "owner"), ("plan", "plan_path"), ("branch", "branch"), ("worktree", "worktree"),
        ("pr", "pr"), ("gh_issue", "gh_issue"), ("release_tag", "release_tag"), ("evidence", "evidence"),
    ):
        value = getattr(a, attr)
        if value is not None:
            changes[column] = text_or_null(value)
    add_tags = clean_tags(a.add_tag)
    remove_tags = clean_tags(a.remove_tag)
    if not changes and not add_tags and not remove_tags and a.parent is None:
        raise usage_error("Nothing to set. Pass at least one field flag.")
    with write_tx(ctx) as conn:
        row = get_row(conn, a.id)
        task_id = row["id"]
        if a.parent is not None:
            parent_id = normalize_id(a.parent) if a.parent.strip() else None
            if parent_id:
                parent = get_row(conn, parent_id)
                if parent["project"] != row["project"]:
                    raise usage_error(f"Parent {parent_id} belongs to another project.")
                assert_parent_allowed(conn, parent, task_id)
            changes["parent_id"] = parent_id
        if add_tags or remove_tags:
            tags = [t for t in json.loads(row["tags"]) if t not in remove_tags]
            tags += [t for t in add_tags if t not in tags]
            changes["tags"] = json.dumps(tags, ensure_ascii=False)
        if "status" in changes and row["status"] in CLOSED:
            changes["closed_at"] = None
        apply_changes(ctx, row, changes)
    task = fetch_task(ctx.conn, task_id)
    return {"task": task}, f"Updated {task_id}."


def insert_comment(ctx, task_id, kind, body, to_agent=None):
    cursor = ctx.conn.execute(
        "INSERT INTO comments(task_id, author, to_agent, kind, body, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, ctx.agent, to_agent, kind, body, now()),
    )
    comment_id = cursor.lastrowid
    log_event(ctx, task_id, "comment", None, f"#{comment_id} {kind}")
    touch(ctx, task_id)
    return comment_id


def fetch_comment(conn, comment_id):
    return comment_dict(get_comment_row(conn, comment_id))


def cmd_comment(ctx):
    a = ctx.args
    kind = choice(a.kind, COMMENT_KINDS, "--kind")
    with write_tx(ctx) as conn:
        task_id = get_row(conn, a.id)["id"]
        comment_id = insert_comment(ctx, task_id, kind, a.text, text_or_null(a.to))
    comment = fetch_comment(ctx.conn, comment_id)
    return {"comment": comment, "task": fetch_task(ctx.conn, task_id)}, f"Comment #{comment_id} added to {task_id}."


def cmd_resolve(ctx):
    with write_tx(ctx) as conn:
        comment = get_comment_row(conn, ctx.args.comment_id)
        if not comment["resolved"]:
            conn.execute("UPDATE comments SET resolved = 1 WHERE id = ?", (comment["id"],))
            log_event(ctx, comment["task_id"], "comment_resolved", None, f"#{comment['id']}")
            touch(ctx, comment["task_id"])
            settle_needs_confirm(ctx, comment["task_id"])
    return (
        {"comment": fetch_comment(ctx.conn, comment["id"]), "task": fetch_task(ctx.conn, comment["task_id"])},
        f"Comment #{comment['id']} resolved.",
    )


def cmd_ask(ctx):
    a = ctx.args
    if not a.to.strip():
        raise usage_error("ask needs --to with an agent name.")
    with write_tx(ctx) as conn:
        row = get_row(conn, a.id)
        if row["status"] in CLOSED:
            raise refused("task_closed", f"{row['id']} is {row['status']}.", status=row["status"])
        comment_id = insert_comment(ctx, row["id"], "question", a.text, a.to.strip())
        apply_changes(ctx, row, {"status": "needs_confirm"})
    return (
        {"comment": fetch_comment(ctx.conn, comment_id), "task": fetch_task(ctx.conn, row["id"])},
        f"Question #{comment_id} on {row['id']} sent to {a.to.strip()}. Status: needs_confirm.",
    )


def cmd_answer(ctx):
    a = ctx.args
    with write_tx(ctx) as conn:
        question = get_comment_row(conn, a.comment_id)
        if question["kind"] != "question":
            raise usage_error(f"Comment #{question['id']} is a {question['kind']}, not a question.")
        if question["resolved"]:
            raise refused("already_resolved", f"Question #{question['id']} is already resolved.")
        task_id = question["task_id"]
        answer_id = insert_comment(ctx, task_id, "answer", a.text, question["author"])
        conn.execute("UPDATE comments SET resolved = 1 WHERE id = ?", (question["id"],))
        log_event(ctx, task_id, "comment_resolved", None, f"#{question['id']}")
        settle_needs_confirm(ctx, task_id)
    task = fetch_task(ctx.conn, task_id)
    return (
        {"comment": fetch_comment(ctx.conn, answer_id), "question_id": question["id"], "task": task},
        f"Answer #{answer_id} added. Question #{question['id']} resolved. {task_id} status: {task['status']}.",
    )


def cmd_inbox(ctx):
    conn = ctx.conn
    agent = ctx.args.for_agent or ctx.agent
    project = ctx.scope()
    where = ["c.resolved = 0", "c.to_agent = ?", f"t.status NOT IN {SQL_CLOSED}"]
    params = [agent]
    add_scope(where, params, project)
    comments = [
        comment_dict(r)
        for r in conn.execute(
            "SELECT c.*, t.project, t.title AS task_title FROM comments c "
            f"JOIN tasks t ON t.id = c.task_id WHERE {' AND '.join(where)} ORDER BY c.id",
            params,
        )
    ]
    where = ["t.owner = ?", "t.status IN ('needs_confirm','needs_review')"]
    params = [agent]
    add_scope(where, params, project)
    tasks = [
        task_dict(r)
        for r in conn.execute(f"{TASK_SELECT} WHERE {' AND '.join(where)} ORDER BY {SQL_ORDER}", params)
    ]
    lines = [comment_line(c) for c in comments] + [task_line(t) for t in tasks]
    return (
        {"for": agent, "comments": comments, "tasks": tasks},
        "\n".join(lines) if lines else f"Inbox of {agent} is empty.",
    )


def assert_no_cycle(conn, from_id, to_id):
    """Refuse a block link when to_id already blocks from_id, directly or not."""
    row = conn.execute(
        """WITH RECURSIVE chain(id) AS (
               SELECT ?
               UNION
               SELECT l.to_id FROM links l JOIN chain c ON l.from_id = c.id WHERE l.type = 'blocks'
           )
           SELECT 1 FROM chain WHERE id = ?""",
        (to_id, from_id),
    ).fetchone()
    if row:
        raise usage_error(f"{from_id} cannot block {to_id}: {to_id} already blocks {from_id}.", "link_cycle")


def link_target(args):
    if args.blocks:
        return "blocks", normalize_id(args.blocks)
    return "relates", normalize_id(args.relates)


def cmd_link(ctx):
    a = ctx.args
    kind, target = link_target(a)
    with write_tx(ctx) as conn:
        source = get_row(conn, a.id)["id"]
        get_row(conn, target)
        if source == target:
            raise usage_error("A task cannot link to itself.")
        if kind == "blocks":
            assert_no_cycle(conn, source, target)
            exists = conn.execute(
                "SELECT 1 FROM links WHERE from_id = ? AND to_id = ? AND type = 'blocks'", (source, target)
            ).fetchone()
        else:
            exists = conn.execute(
                "SELECT 1 FROM links WHERE type = 'relates' AND "
                "((from_id = ? AND to_id = ?) OR (from_id = ? AND to_id = ?))",
                (source, target, target, source),
            ).fetchone()
        if not exists:
            conn.execute("INSERT INTO links(from_id, to_id, type) VALUES (?, ?, ?)", (source, target, kind))
            log_event(ctx, source, "link", None, f"{kind} {target}")
            log_event(ctx, target, "link", None, f"{source} {kind}")
            touch(ctx, source)
            touch(ctx, target)
    return (
        {"link": {"from_id": source, "to_id": target, "type": kind}, "changed": not exists},
        f"{source} {kind} {target}.",
    )


def cmd_unlink(ctx):
    a = ctx.args
    kind, target = link_target(a)
    with write_tx(ctx) as conn:
        source = get_row(conn, a.id)["id"]
        get_row(conn, target)
        if kind == "blocks":
            removed = conn.execute(
                "DELETE FROM links WHERE from_id = ? AND to_id = ? AND type = 'blocks'", (source, target)
            ).rowcount
        else:
            removed = conn.execute(
                "DELETE FROM links WHERE type = 'relates' AND "
                "((from_id = ? AND to_id = ?) OR (from_id = ? AND to_id = ?))",
                (source, target, target, source),
            ).rowcount
        if removed:
            log_event(ctx, source, "link", f"{kind} {target}", None)
            log_event(ctx, target, "link", f"{source} {kind}", None)
            touch(ctx, source)
            touch(ctx, target)
    message = f"Removed {source} {kind} {target}." if removed else f"No {kind} link from {source} to {target}."
    return ({"link": {"from_id": source, "to_id": target, "type": kind}, "changed": bool(removed)}, message)


def cmd_done(ctx):
    a = ctx.args
    evidence = (a.evidence or "").strip()
    if not evidence:
        raise refused(
            "evidence_required",
            "done needs --evidence with the commands you ran and their results.",
        )
    with write_tx(ctx) as conn:
        row = get_row(conn, a.id)
        task_id = row["id"]
        if row["status"] in CLOSED:
            raise refused("task_closed", f"{task_id} is already {row['status']}.", status=row["status"])
        questions = open_question_ids(conn, task_id)
        children = [
            r["id"]
            for r in conn.execute(
                f"SELECT id FROM tasks WHERE parent_id = ? AND status NOT IN {SQL_CLOSED} ORDER BY id",
                (task_id,),
            )
        ]
        if questions or children:
            detail = []
            if questions:
                detail.append("open questions " + ", ".join(f"#{q}" for q in questions))
            if children:
                detail.append("open children " + ", ".join(children))
            joined = " and ".join(detail)
            if not a.force:
                raise refused(
                    "done_refused",
                    f"{task_id} has {joined}. Close them or pass --force.",
                    open_questions=questions,
                    open_children=children,
                )
            log_event(ctx, task_id, "force", None, f"done --force with {joined}")
        apply_changes(
            ctx, row,
            {"status": "done", "evidence": evidence, "closed_at": now(), "claimed_by": None, "claim_expires_at": None},
        )
    task = fetch_task(ctx.conn, task_id)
    return {"task": task}, f"Done {task_id}."


def cmd_drop(ctx):
    a = ctx.args
    reason = (a.reason or "").strip()
    if not reason:
        raise refused("reason_required", "drop needs --reason.")
    with write_tx(ctx) as conn:
        row = get_row(conn, a.id)
        task_id = row["id"]
        if row["status"] in CLOSED:
            raise refused("task_closed", f"{task_id} is already {row['status']}.", status=row["status"])
        insert_comment(ctx, task_id, "note", f"Dropped: {reason}")
        row = get_row(conn, task_id)
        apply_changes(
            ctx, row,
            {"status": "dropped", "closed_at": now(), "claimed_by": None, "claim_expires_at": None},
        )
    task = fetch_task(ctx.conn, task_id)
    return {"task": task}, f"Dropped {task_id}."


def cmd_report(ctx):
    conn = ctx.conn
    project = ctx.scope()
    where, params = [], []
    add_scope(where, params, project)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    by_status = {s: 0 for s in STATUSES}
    for r in conn.execute(f"SELECT t.status, count(*) AS n FROM tasks t {clause} GROUP BY t.status", params):
        by_status[r["status"]] = r["n"]
    open_where = [*where, f"t.status NOT IN {SQL_CLOSED}"]
    open_clause = f"WHERE {' AND '.join(open_where)}"
    by_priority = {p: 0 for p in PRIORITIES}
    for r in conn.execute(
        f"SELECT t.priority, count(*) AS n FROM tasks t {open_clause} GROUP BY t.priority", params
    ):
        by_priority[r["priority"]] = r["n"]
    stale = stale_claims(ctx, project)
    blocked = [
        task_dict(r)
        for r in conn.execute(
            f"{TASK_SELECT} {open_clause} AND (t.status = 'blocked' OR {SQL_HAS_BLOCKER}) ORDER BY {SQL_ORDER}",
            params,
        )
    ]
    questions = [
        comment_dict(r)
        for r in conn.execute(
            "SELECT c.*, t.project, t.title AS task_title FROM comments c JOIN tasks t ON t.id = c.task_id "
            f"{open_clause} AND c.resolved = 0 AND c.to_agent = ? ORDER BY c.id",
            [*params, DEVELOPER],
        )
    ]
    waiting = [
        task_dict(r)
        for r in conn.execute(
            f"{TASK_SELECT} {open_clause} AND ("
            "(t.status = 'needs_review' AND (t.owner IS NULL OR t.owner = ?)) "
            "OR (t.status = 'needs_confirm' AND t.owner = ?)) "
            f"ORDER BY {SQL_ORDER}",
            [*params, DEVELOPER, DEVELOPER],
        )
    ]
    parents = [
        task_dict(r)
        for r in conn.execute(
            f"{TASK_SELECT} {open_clause} AND EXISTS (SELECT 1 FROM tasks c WHERE c.parent_id = t.id) "
            f"ORDER BY {SQL_ORDER}",
            params,
        )
    ]
    left = sum(n for s, n in by_status.items() if s not in CLOSED)
    data = {
        "project": project,
        "counts_by_status": by_status,
        "counts_by_priority": by_priority,
        "tasks_left": left,
        "parents": parents,
        "stale_claims": stale,
        "blocked": blocked,
        "waiting_on_developer": {"questions": questions, "tasks": waiting},
    }
    lines = [f"Project: {project or 'all'}", f"Tasks left: {left}"]
    lines.append("By status: " + ", ".join(f"{k} {v}" for k, v in by_status.items() if v))
    lines.append("Open by priority: " + ", ".join(f"{k} {v}" for k, v in by_priority.items()))
    if parents:
        lines += ["", "Parents with progress:"] + [f"  {task_line(t)}" for t in parents]
    if stale:
        lines += ["", "Stale claims:"] + [f"  {task_line(t)}" for t in stale]
    if blocked:
        lines += ["", "Blocked:"] + [f"  {task_line(t)}" for t in blocked]
    if questions or waiting:
        lines += ["", "Waiting on the developer:"]
        lines += [f"  {comment_line(c)}" for c in questions]
        lines += [f"  {task_line(t)}" for t in waiting]
    return data, "\n".join(lines)


def safe_segments(project):
    parts = []
    for part in re.split(r"[/\\]+", project):
        clean = re.sub(r"[^\w.\-]", "_", part).strip(".")
        if clean:
            parts.append(clean)
    return parts or ["default"]


def clean_topic(raw):
    topic = re.sub(r"\s+", "-", raw.strip()).lower()
    if topic.endswith(".md"):
        topic = topic[:-3]
    if not re.fullmatch(r"\w[\w.\-]*", topic) or topic.endswith(".") or topic in WINDOWS_RESERVED:
        raise usage_error("A topic uses letters, digits, spaces, dots, dashes and underscores only.")
    if len(topic.encode("utf-8")) > MAX_TOPIC_BYTES:
        # File names stop at 255 bytes on Linux and macOS; the task ID and date come on top.
        raise usage_error(f"A topic can be at most {MAX_TOPIC_BYTES} bytes, about 60 Arabic letters.")
    return topic


def cmd_plan_path(ctx):
    a = ctx.args
    topic = clean_topic(a.topic)
    if a.all:
        raise usage_error("plan-path needs one project. Pass --project, not --all.")
    task = get_row(ctx.conn, a.task) if a.task else None
    project = task["project"] if task else ctx.project()
    if not ctx.conn.execute("SELECT 1 FROM projects WHERE name = ?", (project,)).fetchone():
        with write_tx(ctx) as conn:
            ensure_project(conn, project)
    short = ctx.conn.execute("SELECT short_name FROM projects WHERE name = ?", (project,)).fetchone()[0]
    today = datetime.now().strftime("%Y-%m-%d")
    folder = qtask_home() / "plans" / short / today
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (f"{task['id']}-{topic}.md" if task else f"{topic}.md")
    title = task["title"] if task else topic.replace("-", " ")
    task_label = task["id"] if task else ""
    with contextlib.suppress(FileExistsError):
        with open(path, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(
                f"# {title}\n\n"
                f"**Project:** {project}\n**Date:** {today}\n**Task:** {task_label}\n\n"
                "## Goal\n\n## Steps\n\n## Evidence\n"
            )
        a.plan_created = True
    shown = path.as_posix()
    linked = False
    if task and not task["plan_path"]:
        with write_tx(ctx) as conn:
            row = get_row(conn, task["id"])
            if not row["plan_path"]:
                linked = apply_changes(ctx, row, {"plan_path": shown})
    data = {
        "path": shown,
        "created": getattr(a, "plan_created", False),
        "project": project,
        "task_id": task["id"] if task else None,
        "plan_path_set": linked,
    }
    return data, shown


def path_key(raw):
    return os.path.normcase(os.path.normpath(msys_to_windows(raw)))


def first_heading(text):
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match and not fenced:
            return match.group(1)
    return None


def plan_folders(conn):
    """Map each plans subfolder, short or older owner/repo style, to its project."""
    rows = conn.execute("SELECT name, short_name FROM projects ORDER BY created_at, name").fetchall()
    folders = {"/".join(safe_segments(r["name"])): r["name"] for r in rows}
    folders.update({r["short_name"]: r["name"] for r in rows})
    return folders


def plan_task(conn, file_name, file_path, project, by_path):
    """Find a plan's task by its file name, then by a task's plan_path."""
    match = re.match(r"^([A-Z][A-Z0-9]*-\d+)-", file_name)
    if match:
        row = conn.execute("SELECT id, status, project FROM tasks WHERE id = ?", (match.group(1),)).fetchone()
        if row and (project is None or row["project"] == project):
            return row
    return by_path.get(path_key(str(file_path)))


def cmd_plans(ctx):
    a = ctx.args
    conn = ctx.conn
    if a.day:
        try:
            datetime.strptime(a.day, "%Y-%m-%d")
        except ValueError:
            raise usage_error("--day must look like YYYY-MM-DD.") from None
    project = ctx.scope()
    needle = a.search.strip().casefold() if a.search else None
    folders = plan_folders(conn)
    by_path = {}
    for row in conn.execute(
        "SELECT id, status, project, plan_path FROM tasks WHERE plan_path IS NOT NULL ORDER BY id"
    ):
        by_path.setdefault(path_key(row["plan_path"]), row)
    root = qtask_home() / "plans"
    found = []
    for file_path in root.rglob("*.md") if root.exists() else []:
        parts = file_path.relative_to(root).parts[:-1]
        try:
            stat = file_path.stat()
            text = file_path.read_bytes().decode("utf-8-sig", errors="replace")
        except OSError:
            continue
        if parts and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[-1]):
            day, folder = parts[-1], "/".join(parts[:-1])
        else:
            day = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d")
            folder = "/".join(parts)
        owner = folders.get(folder)
        if (project is not None and owner != project) or (a.day and day != a.day):
            continue
        task = plan_task(conn, file_path.name, file_path, owner, by_path)
        title = first_heading(text) or file_path.stem
        if needle is not None:
            haystack = "\n".join((file_path.name, title, task["id"] if task else "", text)).casefold()
            if needle not in haystack:
                continue
        plan = {
            "date": day,
            "project": owner,
            "task_id": task["id"] if task else None,
            "status": task["status"] if task else None,
            "title": title,
            "path": file_path.as_posix(),
        }
        found.append(((day, stat.st_mtime_ns, plan["path"]), plan))
    found.sort(key=lambda item: item[0], reverse=True)
    plans = [plan for _, plan in found]
    lines = []
    for plan in plans:
        link = f"{plan['task_id']} {plan['status']}" if plan["task_id"] else "-"
        lines.append(f"{plan['date']}  {link}  {plan['title']}  {plan['path']}")
    return {"plans": plans, "count": len(plans)}, "\n".join(lines) if lines else "No plans."


def tree_node(conn, task, seen):
    seen = seen | {task["id"]}
    rows = conn.execute(f"{TASK_SELECT} WHERE t.parent_id = ? ORDER BY {SQL_ORDER}", (task["id"],)).fetchall()
    children = [task_dict(r) for r in rows if r["id"] not in seen]
    return {**task, "children": [tree_node(conn, child, seen) for child in children]}


def tree_lines(node, depth=0):
    lines = ["  " * depth + task_line(node)]
    for child in node["children"]:
        lines += tree_lines(child, depth + 1)
    return lines


def cmd_tree(ctx):
    conn = ctx.conn
    if ctx.args.id:
        roots = [fetch_task(conn, get_row(conn, ctx.args.id)["id"])]
    else:
        where = [
            "t.parent_id IS NULL",
            f"t.status NOT IN {SQL_CLOSED}",
            "EXISTS (SELECT 1 FROM tasks c WHERE c.parent_id = t.id)",
        ]
        params = []
        add_scope(where, params, ctx.scope())
        rows = conn.execute(f"{TASK_SELECT} WHERE {' AND '.join(where)} ORDER BY {SQL_ORDER}", params)
        roots = [task_dict(r) for r in rows.fetchall()]
    tree = [tree_node(conn, root, frozenset()) for root in roots]
    lines = [line for node in tree for line in tree_lines(node)]
    return {"tree": tree, "count": len(tree)}, "\n".join(lines) if lines else "No open parents."


def cmd_backup(ctx):
    db_path, jsonl_path = run_backup(ctx.conn)
    return (
        {"db": db_path.as_posix(), "jsonl": jsonl_path.as_posix()},
        f"{db_path.as_posix()}\n{jsonl_path.as_posix()}",
    )


# -------------------------------------------------------------------- parser


class Parser(argparse.ArgumentParser):
    def error(self, message):
        if "--json" in sys.argv[1:]:
            payload = {"ok": False, "error": {"code": "bad_usage", "message": message, "exit_code": EXIT_USAGE}}
            print(json.dumps(payload, ensure_ascii=False))
        else:
            self.print_usage(sys.stderr)
            print(f"qtask: {message}", file=sys.stderr)
        sys.exit(EXIT_USAGE)


def add_global_flags(parser, top_level):
    def default(value):
        return value if top_level else argparse.SUPPRESS

    parser.add_argument("--json", action="store_true", default=default(False), help="print JSON")
    parser.add_argument("--project", default=default(None), help="project name, for example owner/repo")
    parser.add_argument("--all", action="store_true", default=default(False), help="use every project")
    parser.add_argument("--agent", default=default(None), help="agent name, else QTASK_AGENT, else unknown")


def add_filters(parser):
    parser.add_argument("--kind")
    parser.add_argument("--pri", "--priority", dest="pri")
    parser.add_argument("--side")
    parser.add_argument("--tag", action="append")
    parser.add_argument("--owner")
    parser.add_argument("--claimed-by")
    parser.add_argument("--parent")
    parser.add_argument("--search")
    parser.add_argument("--limit", type=int)


def add_text(parser, help_text):
    parser.add_argument("text", nargs="?", help=help_text)
    parser.add_argument("--body-file", help="read the text from a UTF-8 file, or stdin with -")


def build_parser():
    parser = Parser(
        prog="qtask", description="Local task tracker for AI coding agents and the people they work with.",
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"qtask {__version__}")
    add_global_flags(parser, top_level=True)
    subs = parser.add_subparsers(dest="command", required=True, metavar="COMMAND", parser_class=Parser)

    def command(name, func, help_text):
        sub = subs.add_parser(name, help=help_text, description=help_text, allow_abbrev=False)
        add_global_flags(sub, top_level=False)
        sub.set_defaults(func=func)
        return sub

    p = command("add", cmd_add, "Create a task.")
    p.add_argument("title")
    p.add_argument("--kind", default="task", help=", ".join(KINDS))
    p.add_argument("--pri", "--priority", dest="pri", default="P2", help=", ".join(PRIORITIES))
    p.add_argument("--side", help=", ".join(SIDES))
    p.add_argument("--tag", action="append", help="repeatable")
    p.add_argument("--body")
    p.add_argument("--body-file", help="read the body from a UTF-8 file, or stdin with -")
    p.add_argument("--plan", help="plan file path, stored as written")
    p.add_argument("--parent")
    p.add_argument("--status", default="ready", help="any open status, default ready")
    p.add_argument("--owner")
    p.add_argument("--branch")
    p.add_argument("--pr")

    p = command("ls", cmd_ls, "List tasks. Hides done and dropped unless asked.")
    add_filters(p)
    p.add_argument("--status", action="append", help="repeatable")
    p.add_argument("--include-closed", action="store_true")

    p = command("ready", cmd_ready, "List tasks an agent can claim now.")
    add_filters(p)

    p = command("show", cmd_show, "Show a task with children, comments, links and events.")
    p.add_argument("id")

    p = command("search", cmd_search, "Search tasks and comments.")
    p.add_argument("text")

    p = command("claim", cmd_claim, "Claim a task. Exit 3 when another agent holds it.")
    p.add_argument("id")
    p.add_argument("--ttl-hours", type=float, default=2.0)

    p = command("release", cmd_release, "Clear the claim and return the task to ready.")
    p.add_argument("id")
    p.add_argument("--force", action="store_true", help="release another agent's valid claim")

    p = command("set", cmd_set, "Change task fields. An empty value clears an optional field.")
    p.add_argument("id")
    p.add_argument("--title")
    p.add_argument("--body")
    p.add_argument("--body-file", help="read the body from a UTF-8 file, or stdin with -")
    p.add_argument("--kind")
    p.add_argument("--status", help="any open status; use done or drop to close")
    p.add_argument("--pri", "--priority", dest="pri")
    p.add_argument("--side")
    p.add_argument("--owner")
    p.add_argument("--plan")
    p.add_argument("--branch")
    p.add_argument("--worktree")
    p.add_argument("--pr")
    p.add_argument("--gh-issue")
    p.add_argument("--release-tag")
    p.add_argument("--visibility", help=", ".join(VISIBILITIES))
    p.add_argument("--evidence")
    p.add_argument("--parent")
    p.add_argument("--add-tag", action="append", help="repeatable")
    p.add_argument("--remove-tag", action="append", help="repeatable")

    p = command("comment", cmd_comment, "Add a comment to a task.")
    p.add_argument("id")
    add_text(p, "comment text")
    p.add_argument("--to", help="address the comment to an agent")
    p.add_argument("--kind", default="note", help=", ".join(COMMENT_KINDS))

    p = command("resolve", cmd_resolve, "Mark a comment resolved.")
    p.add_argument("comment_id")

    p = command("ask", cmd_ask, "Ask a question. The task moves to needs_confirm.")
    p.add_argument("id")
    add_text(p, "question text")
    p.add_argument("--to", required=True, help="agent or developer who should answer")

    p = command("answer", cmd_answer, "Answer a question and resolve it.")
    p.add_argument("comment_id")
    add_text(p, "answer text")

    p = command("inbox", cmd_inbox, "Questions and comments for an agent, plus owned tasks waiting on them.")
    p.add_argument("--for", dest="for_agent", help="agent name, default the current agent")

    for name, func, text in (
        ("link", cmd_link, "Link two tasks."),
        ("unlink", cmd_unlink, "Remove a link between two tasks."),
    ):
        p = command(name, func, text)
        p.add_argument("id")
        group = p.add_mutually_exclusive_group(required=True)
        group.add_argument("--blocks", metavar="ID2")
        group.add_argument("--relates", metavar="ID2")

    p = command("done", cmd_done, "Close a task with evidence.")
    p.add_argument("id")
    p.add_argument("--evidence", help="commands you ran and their results; required")
    p.add_argument("--force", action="store_true", help="close despite open questions or children")

    p = command("drop", cmd_drop, "Close a task without doing it.")
    p.add_argument("id")
    p.add_argument("--reason", help="required")

    command("report", cmd_report, "Counts, stale claims, blocked tasks and what waits on the developer.")

    p = command("plan-path", cmd_plan_path, "Create the dated plan file and print its path.")
    p.add_argument("topic")
    p.add_argument("--task", metavar="ID", help="name the file after this task and link it")

    p = command("plans", cmd_plans, "List plan files, newest first.")
    p.add_argument("--day", metavar="YYYY-MM-DD")
    p.add_argument("--search", metavar="TEXT")

    p = command("tree", cmd_tree, "Show a parent and its subtasks, or all open parents in the project.")
    p.add_argument("id", nargs="?")

    command("backup", cmd_backup, "Write a database backup and a JSONL export.")
    return parser


# ---------------------------------------------------------------------- main


def force_utf8():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")
    if sys.stdin is not None and hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")


def execute(args):
    """Run a command, retrying the whole unit when SQLite reports a transient lock."""
    deadline = time.monotonic() + RETRY_SECONDS
    while True:
        conn = ctx = None
        try:
            conn = open_db()
            ctx = Ctx(conn, args)
            result = args.func(ctx)
            if ctx.backup_due:
                try:
                    run_backup(conn)
                except (sqlite3.Error, OSError) as exc:
                    print(f"qtask: warning: the daily backup failed: {exc}", file=sys.stderr)
            return result
        except sqlite3.OperationalError as exc:
            if not transient_db_error(exc):
                raise
            if ctx is not None and ctx.committed:
                # Running the command again would repeat a change that is already saved.
                raise QtaskError(
                    "db_busy_after_write",
                    f"The change was saved, but reading it back failed: {exc}. Check it with qtask show.",
                ) from exc
            if time.monotonic() > deadline:
                raise QtaskError("db_busy", f"The database stayed busy for {RETRY_SECONDS} s: {exc}") from exc
            time.sleep(random.uniform(0.05, 0.3))
        finally:
            if conn is not None:
                with contextlib.suppress(sqlite3.Error):
                    conn.close()


def main(argv=None):
    force_utf8()
    if os.name != "nt":
        os.umask(0o077)  # Tasks, plans and backups are private: only this user may read them.
    args = build_parser().parse_args(argv)
    args.agent = (args.agent or os.environ.get("QTASK_AGENT") or UNKNOWN_AGENT).strip() or UNKNOWN_AGENT
    try:
        load_inputs(args)
        data, text = execute(args)
    except QtaskError as exc:
        return fail(args, exc.code, exc.message, exc.exit_code, exc.extra)
    except sqlite3.Error as exc:
        return fail(args, "db_error", str(exc), EXIT_ERROR, {})
    except KeyboardInterrupt:
        return fail(args, "interrupted", "Stopped with Ctrl-C.", EXIT_INTERRUPTED, {})
    except OSError as exc:
        return fail(args, "os_error", str(exc), EXIT_ERROR, {})
    except (ValueError, ArithmeticError, RuntimeError) as exc:
        # Agents parse --json, so an unexpected error still comes back as JSON, not a traceback.
        return fail(args, "unexpected", f"{type(exc).__name__}: {exc}", EXIT_ERROR, {})
    try:
        if args.json:
            print(json.dumps({"ok": True, **data}, ensure_ascii=False, indent=2))
        else:
            print(text)
        sys.stdout.flush()
    except BrokenPipeError:
        with contextlib.suppress(OSError):
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
    return EXIT_OK


def fail(args, code, message, exit_code, extra):
    if args.json:
        error = {"code": code, "message": message, "exit_code": exit_code, **extra}
        print(json.dumps({"ok": False, "error": error}, ensure_ascii=False, indent=2))
    else:
        print(f"qtask: {message}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
