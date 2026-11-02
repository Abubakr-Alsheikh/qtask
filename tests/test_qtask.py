"""Tests for qtask. Run from the repo root with: python tests/test_qtask.py

Every test uses a temp QTASK_HOME. A module-level check fails the run if test data
reaches the real database.
"""

import base64
import contextlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
QTASK = ROOT / "qtask.py"
LAUNCHERS = ROOT / "launchers"
PYTHON = [sys.executable, "-I", "-X", "utf8"]
REAL_DB = Path(os.environ.get("QTASK_HOME") or Path.home() / ".qtask") / "qtask.db"
WINDOWS = os.name == "nt"


def find_bash():
    """Git Bash on Windows (never the WSL bash.exe), else the bash on PATH."""
    if not WINDOWS:
        return shutil.which("bash")
    git = shutil.which("git")
    candidates = [Path(git).resolve().parent.parent / "bin" / "bash.exe"] if git else []
    candidates.append(Path(r"C:\Program Files\Git\bin\bash.exe"))
    return next((str(p) for p in candidates if p.exists()), None)


def sqlite_has_fts5():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def force_rmtree(path):
    """rmtree that also removes read-only files, such as git objects on Windows."""

    def retry(func, target, _exc):
        with contextlib.suppress(OSError):
            os.chmod(target, stat.S_IWRITE)
            func(target)

    with contextlib.suppress(OSError):
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=retry)
        else:
            shutil.rmtree(path, onerror=retry)


BASH = find_bash()
HAS_FTS5 = sqlite_has_fts5()
ARABIC ="إصلاح زر الإرسال «ذاكر» في الاختبار"
RACE_ROUNDS = 5
RACE_PROCESSES = 20


def tearDownModule():
    """Other sessions write to the real database, so look for test data in it, not for changes."""
    if not REAL_DB.exists():
        return
    conn = sqlite3.connect(f"{REAL_DB.as_uri()}?mode=ro", uri=True, timeout=15)
    try:
        found = conn.execute("SELECT count(*) FROM tasks WHERE project = 'acme/app'").fetchone()[0]
    finally:
        conn.close()
    if found:
        raise AssertionError("The tests wrote to the real qtask database.")


class Base(unittest.TestCase):
    project = "acme/app"

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="qtask-test-")
        self.addCleanup(force_rmtree, self.home)
        self.env = {**os.environ, "QTASK_HOME": self.home}
        self.env.pop("QTASK_AGENT", None)
        self.assertNotEqual(Path(self.home).resolve(), REAL_DB.parent.resolve())

    def run_qtask(self, *args, agent=None, cwd=None, env=None, stdin=None):
        command = [*PYTHON, str(QTASK), *args]
        if agent:
            command += ["--agent", agent]
        return subprocess.run(
            command, capture_output=True, encoding="utf-8", cwd=cwd or self.home,
            env=env or self.env, input=stdin,
        )

    def js(self, *args, agent=None, cwd=None, expect=0, env=None):
        result = self.run_qtask(*args, "--json", agent=agent, cwd=cwd, env=env)
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def add(self, title="task", *extra, project=None):
        return self.js("add", title, "--project", project or self.project, *extra)["task"]["id"]

    def ls(self, command="ls", *extra):
        return [t["id"] for t in self.js(command, "--project", self.project, *extra)["tasks"]]

    def sql(self, statement, *params):
        conn = sqlite3.connect(Path(self.home) / "qtask.db", timeout=15, isolation_level=None)
        try:
            return conn.execute(statement, params).fetchall()
        finally:
            conn.close()

    def assert_no_lock_error(self, text):
        text = text.lower()
        for phrase in ("database is locked", "database is busy", "db_busy", "locking protocol", "traceback"):
            self.assertNotIn(phrase, text)

    def race(self, commands):
        """Start processes that wait for one shared moment, so their work overlaps."""
        start_at = time.time() + 3.0
        code = (
            "import runpy, sys, time\n"
            f"t = {start_at!r}\n"
            "while time.time() < t: time.sleep(0.001)\n"
            "sys.argv = sys.argv[1:]\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n"
        )
        processes = [
            subprocess.Popen(
                [*PYTHON, "-c", code, str(QTASK), *command],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
                cwd=self.home, env=self.env,
            )
            for command in commands
        ]
        return [(p.communicate(timeout=180), p.returncode) for p in processes]


class ClaimRace(Base):
    def test_one_winner_on_existing_db(self):
        for round_number in range(RACE_ROUNDS):
            task = self.add(f"race {round_number}")
            results = self.race(
                [["claim", task, "--agent", f"agent-{i}"] for i in range(RACE_PROCESSES)]
            )
            self.check_one_winner(results, task)

    def test_one_winner_with_wal_files_gone(self):
        """All 20 open the database together, so each may be first or last connection."""
        task = self.add("cold race")
        for suffix in ("-wal", "-shm"):
            self.assertFalse((Path(self.home) / f"qtask.db{suffix}").exists())
        results = self.race([["claim", task, "--agent", f"agent-{i}"] for i in range(RACE_PROCESSES)])
        self.check_one_winner(results, task)

    def check_one_winner(self, results, task):
        codes = sorted(code for _, code in results)
        self.assertEqual(codes, [0] + [3] * (RACE_PROCESSES - 1), results)
        for (out, err), _ in results:
            self.assert_no_lock_error(out + err)
        row = self.js("show", task)["task"]
        self.assertEqual(row["status"], "in_progress")
        self.assertTrue(row["claimed_by"].startswith("agent-"))
        winners = self.sql(
            "SELECT count(*) FROM events WHERE task_id = ? AND field = 'claimed_by'", task
        )[0][0]
        self.assertEqual(winners, 1)


class AddRace(Base):
    def test_cold_start_unique_ids(self):
        """The database does not exist yet, so this also races the migration."""
        results = self.race(
            [["add", f"task {i}", "--project", self.project, "--json"] for i in range(RACE_PROCESSES)]
        )
        for (out, err), code in results:
            self.assertEqual(code, 0, out + err)
            self.assert_no_lock_error(out + err)
        ids = [json.loads(out)["task"]["id"] for (out, _), _ in results]
        self.assertEqual(len(set(ids)), RACE_PROCESSES)
        self.assertEqual(sorted(int(i.split("-")[1]) for i in ids), list(range(1, RACE_PROCESSES + 1)))
        self.assertEqual(self.sql("SELECT count(*) FROM tasks")[0][0], RACE_PROCESSES)
        self.assertEqual(self.sql("SELECT count(*) FROM schema_version")[0][0], 2)

    def test_daily_backup_runs_once_under_race(self):
        self.race([["add", f"task {i}", "--project", self.project] for i in range(RACE_PROCESSES)])
        self.assertEqual(len(list(Path(self.home, "backups").glob("qtask-*.db"))), 1)


class Claims(Base):
    def test_expired_claim_takeover_logs_event(self):
        task = self.add()
        self.js("claim", task, agent="alice")
        self.js("claim", task, agent="bob", expect=3)
        self.sql("UPDATE tasks SET claim_expires_at = '2000-01-01T00:00:00Z' WHERE id = ?", task)
        result = self.js("claim", task, agent="bob")["task"]
        self.assertEqual(result["claimed_by"], "bob")
        events = self.js("show", task)["events"]
        takeover = [e for e in events if e["field"] == "claim_takeover"]
        self.assertEqual(len(takeover), 1)
        self.assertEqual((takeover[0]["old"], takeover[0]["new"]), ("alice", "bob"))

    def test_same_agent_extends(self):
        task = self.add()
        first = self.js("claim", task, "--ttl-hours", "1", agent="alice")["task"]
        second = self.js("claim", task, "--ttl-hours", "5", agent="alice")["task"]
        self.assertGreater(second["claim_expires_at"], first["claim_expires_at"])
        self.assertEqual(second["claimed_by"], "alice")

    def test_conflict_message_and_json(self):
        task = self.add()
        self.js("claim", task, agent="alice")
        result = self.run_qtask("claim", task, agent="bob")
        self.assertEqual(result.returncode, 3)
        self.assertIn("claimed by alice", result.stderr)
        error = self.js("claim", task, agent="bob", expect=3)["error"]
        self.assertEqual(error["code"], "claim_conflict")
        self.assertEqual(error["claimed_by"], "alice")

    def test_unknown_agents_do_not_share_a_claim(self):
        task = self.add()
        self.js("claim", task)
        self.js("claim", task, expect=3)

    def test_release(self):
        task = self.add()
        self.js("claim", task, agent="alice")
        self.js("release", task, agent="bob", expect=3)
        released = self.js("release", task, agent="alice")["task"]
        self.assertEqual(released["status"], "ready")
        self.assertIsNone(released["claimed_by"])
        self.assertIsNone(released["claim_expires_at"])
        self.js("claim", task, agent="alice")
        self.assertEqual(self.js("release", task, "--force", agent="bob")["task"]["status"], "ready")

    def test_closed_or_blocked_refused(self):
        closed = self.add()
        self.js("done", closed, "--evidence", "ran it")
        self.assertEqual(self.js("claim", closed, agent="a", expect=4)["error"]["code"], "task_closed")
        held = self.add()
        self.js("set", held, "--status", "blocked")
        self.assertEqual(self.js("claim", held, agent="a", expect=4)["error"]["code"], "task_blocked")

    def test_ready_hides_valid_claim_and_reports_expired(self):
        task = self.add()
        self.js("set", task, "--status", "ready")
        self.sql(
            "UPDATE tasks SET claimed_by = 'x', claim_expires_at = '2999-01-01T00:00:00Z' WHERE id = ?", task
        )
        self.assertNotIn(task, self.ls("ready"))
        self.sql("UPDATE tasks SET claim_expires_at = '2000-01-01T00:00:00Z' WHERE id = ?", task)
        data = self.js("ready", "--project", self.project)
        self.assertIn(task, [t["id"] for t in data["tasks"]])
        self.assertEqual(data["stale_claims"], [task])


class Blockers(Base):
    def test_blocker_flow(self):
        blocker, blocked = self.add("blocker"), self.add("blocked")
        self.js("link", blocker, "--blocks", blocked)
        self.assertNotIn(blocked, self.ls("ready"))
        self.assertIn(blocker, self.ls("ready"))
        self.assertEqual(self.js("claim", blocked, agent="a", expect=4)["error"]["blocked_by"], [blocker])
        self.js("done", blocker, "--evidence", "ran it")
        self.assertIn(blocked, self.ls("ready"))
        self.js("claim", blocked, agent="a")

    def test_dropping_blocker_frees_and_unlink_frees(self):
        a, b, c = self.add("a"), self.add("b"), self.add("c")
        self.js("link", a, "--blocks", b)
        self.js("link", a, "--blocks", c)
        self.js("drop", a, "--reason", "not needed")
        self.assertIn(b, self.ls("ready"))
        d, e = self.add("d"), self.add("e")
        self.js("link", d, "--blocks", e)
        self.js("unlink", d, "--blocks", e)
        self.assertIn(e, self.ls("ready"))

    def test_cycles_and_relates(self):
        a, b, c = self.add("a"), self.add("b"), self.add("c")
        self.js("link", a, "--blocks", b)
        self.js("link", b, "--blocks", c)
        self.js("link", c, "--blocks", a, expect=2)
        self.js("link", a, "--relates", c)
        self.assertEqual(self.js("show", c)["links"]["relates"], [a])
        self.assertIn(c, self.ls("ls"))


class Done(Base):
    def test_requires_evidence(self):
        task = self.add()
        self.assertEqual(self.js("done", task, expect=4)["error"]["code"], "evidence_required")
        self.js("done", task, "--evidence", "   ", expect=4)
        self.js("done", task, "--force", expect=4)
        self.assertEqual(self.js("show", task)["task"]["status"], "ready")

    def test_open_question_refuses_and_force_logs(self):
        task = self.add()
        self.js("ask", task, "why?", "--to", "developer", agent="a")
        error = self.js("done", task, "--evidence", "ran it", expect=4)["error"]
        self.assertEqual(error["code"], "done_refused")
        self.assertEqual(len(error["open_questions"]), 1)
        done = self.js("done", task, "--evidence", "ran it", "--force", agent="a")["task"]
        self.assertEqual(done["status"], "done")
        forced = [e for e in self.js("show", task)["events"] if e["field"] == "force"]
        self.assertEqual(len(forced), 1)
        self.assertEqual(forced[0]["actor"], "a")

    def test_open_child_refuses(self):
        parent = self.add("parent")
        child = self.add("child", "--parent", parent)
        self.assertEqual(self.js("done", parent, "--evidence", "e", expect=4)["error"]["open_children"], [child])
        self.js("done", child, "--evidence", "e")
        done = self.js("done", parent, "--evidence", "e")["task"]
        self.assertIsNotNone(done["closed_at"])
        self.assertIsNone(done["claimed_by"])
        self.js("done", parent, "--evidence", "e", expect=4)

    def test_set_cannot_close(self):
        task = self.add()
        self.js("set", task, "--status", "done", expect=2)
        self.js("add", "x", "--project", self.project, "--status", "dropped", expect=2)

    def test_drop_requires_reason_and_reopen(self):
        task = self.add()
        self.js("drop", task, expect=4)
        dropped = self.js("drop", task, "--reason", "duplicate")["task"]
        self.assertEqual(dropped["status"], "dropped")
        self.assertIn("duplicate", self.js("show", task)["comments"][0]["body"])
        self.assertNotIn(task, self.ls("ls"))
        self.assertIn(task, self.ls("ls", "--include-closed"))
        self.assertIn(task, self.ls("ls", "--status", "dropped"))
        reopened = self.js("set", task, "--status", "ready")["task"]
        self.assertIsNone(reopened["closed_at"])


class AskAnswer(Base):
    def test_unclaimed_returns_to_ready(self):
        task = self.add()
        comment = self.js("ask", task, "which?", "--to", "bob", agent="alice")["comment"]
        self.assertEqual(self.js("show", task)["task"]["status"], "needs_confirm")
        data = self.js("answer", str(comment["id"]), "this one", agent="bob")
        self.assertEqual(data["task"]["status"], "ready")
        self.assertEqual(data["comment"]["to_agent"], "alice")
        self.js("answer", str(comment["id"]), "again", agent="bob", expect=4)

    def test_claimed_returns_to_in_progress(self):
        task = self.add()
        self.js("claim", task, agent="alice")
        comment = self.js("ask", task, "which?", "--to", "bob", agent="alice")["comment"]
        self.assertEqual(self.js("show", task)["task"]["status"], "needs_confirm")
        self.assertEqual(
            self.js("answer", str(comment["id"]), "this", agent="bob")["task"]["status"], "in_progress"
        )

    def test_status_waits_for_last_open_question(self):
        task = self.add()
        first = self.js("ask", task, "one?", "--to", "bob")["comment"]["id"]
        second = self.js("ask", task, "two?", "--to", "bob")["comment"]["id"]
        self.assertEqual(self.js("answer", str(first), "a")["task"]["status"], "needs_confirm")
        self.assertEqual(self.js("answer", str(second), "b")["task"]["status"], "ready")

    def test_lease_extension_keeps_needs_confirm(self):
        task = self.add()
        self.js("claim", task, agent="alice")
        self.js("ask", task, "which?", "--to", "bob", agent="alice")
        self.assertEqual(self.js("claim", task, agent="alice")["task"]["status"], "needs_confirm")

    def test_claim_sets_in_progress_with_a_plain_question_comment(self):
        task = self.add()
        self.js("comment", task, "open point", "--kind", "question")
        self.assertEqual(self.js("claim", task, agent="alice")["task"]["status"], "in_progress")

    def test_ask_needs_a_target(self):
        task = self.add()
        self.js("ask", task, "which?", "--to", "  ", expect=2)
        self.assertEqual(self.js("show", task)["task"]["status"], "ready")

    def test_release_keeps_needs_confirm_while_question_is_open(self):
        task = self.add()
        self.js("claim", task, agent="alice")
        self.js("ask", task, "which?", "--to", "bob", agent="alice")
        self.assertEqual(self.js("release", task, agent="alice")["task"]["status"], "needs_confirm")

    def test_resolve_settles_status(self):
        task = self.add()
        comment = self.js("ask", task, "which?", "--to", "bob")["comment"]["id"]
        self.assertEqual(self.js("resolve", str(comment))["task"]["status"], "ready")

    def test_inbox_contents(self):
        asked = self.add("asked")
        noted = self.add("noted")
        owned_confirm = self.add("owned confirm", "--owner", "bob")
        owned_review = self.add("owned review", "--owner", "bob", "--status", "needs_review")
        other_owner = self.add("other", "--owner", "carol", "--status", "needs_review")
        question = self.js("ask", asked, "q?", "--to", "bob", agent="alice")["comment"]["id"]
        note = self.js("comment", noted, "fyi", "--to", "bob", agent="alice")["comment"]["id"]
        self.js("comment", noted, "for carol", "--to", "carol", agent="alice")
        self.js("comment", noted, "for nobody", agent="alice")
        self.js("set", owned_confirm, "--status", "needs_confirm")
        inbox = self.js("inbox", "--for", "bob", "--project", self.project)
        self.assertEqual(sorted(c["id"] for c in inbox["comments"]), sorted([question, note]))
        self.assertEqual(sorted(t["id"] for t in inbox["tasks"]), sorted([owned_confirm, owned_review]))
        self.assertNotIn(other_owner, [t["id"] for t in inbox["tasks"]])
        self.js("resolve", str(note))
        self.js("answer", str(question), "answered", agent="bob")
        inbox = self.js("inbox", "--for", "bob", "--project", self.project)
        self.assertEqual(inbox["comments"], [])
        alice = self.js("inbox", "--for", "alice", "--project", self.project)
        self.assertEqual([c["kind"] for c in alice["comments"]], ["answer"])

    def test_inbox_defaults_to_current_agent(self):
        task = self.add()
        self.js("comment", task, "hi", "--to", "dana")
        self.assertEqual(len(self.js("inbox", "--project", self.project, agent="dana")["comments"]), 1)


class ProjectDetection(Base):
    def git(self, cwd, *args):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=self.env)

    def temp_repo(self, remote=None):
        repo = tempfile.mkdtemp(prefix="qtask-repo-")
        self.addCleanup(force_rmtree, repo)
        self.git(repo, "init", "-q")
        if remote:
            self.git(repo, "remote", "add", "origin", remote)
        return repo

    def test_other_remote_stays_separate_and_all_sees_both(self):
        one = self.temp_repo("https://github.com/acme/Quill.git")
        two = self.temp_repo("git@github.com:other/thing.git")
        a = self.js("add", "one", cwd=one)["task"]
        b = self.js("add", "two", cwd=two)["task"]
        self.assertEqual((a["project"], b["project"]), ("acme/Quill", "other/thing"))
        self.assertEqual((a["id"], b["id"]), ("QUI-1", "THI-1"))
        self.assertEqual([t["id"] for t in self.js("ls", cwd=one)["tasks"]], ["QUI-1"])
        self.assertEqual([t["id"] for t in self.js("ls", cwd=two)["tasks"]], ["THI-1"])
        self.assertEqual(sorted(t["id"] for t in self.js("ls", "--all", cwd=one)["tasks"]), ["QUI-1", "THI-1"])
        self.assertEqual(self.js("report", "--all", cwd=two)["tasks_left"], 2)

    def test_temp_worktree_without_remote_uses_main_folder(self):
        repo = self.temp_repo()
        self.git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
        tree = repo + "-tree"
        self.git(repo, "worktree", "add", "-q", tree)
        self.addCleanup(force_rmtree, tree)
        main_project = self.js("add", "main", cwd=repo)["task"]["project"]
        tree_project = self.js("add", "tree", cwd=tree)["task"]["project"]
        self.assertEqual(main_project, tree_project)
        self.assertEqual(Path(main_project).name, Path(repo).name)

    def test_outside_repo_needs_project_or_all(self):
        outside = tempfile.mkdtemp(prefix="qtask-outside-")
        self.addCleanup(force_rmtree, outside)
        self.assertEqual(self.js("ls", cwd=outside, expect=2)["error"]["code"], "project_required")
        self.assertEqual(self.js("add", "x", cwd=outside, expect=2)["error"]["code"], "project_required")
        self.assertEqual(self.js("ls", "--all", cwd=outside)["count"], 0)
        task = self.add()
        self.assertEqual(self.js("show", task, cwd=outside)["task"]["id"], task)

    def test_remote_url_forms(self):
        sys.path.insert(0, str(ROOT))
        try:
            from qtask import normalize_remote
        finally:
            sys.path.pop(0)
        for url, slug in (
            ("https://github.com/acme/Quill.git", "acme/Quill"),
            ("https://token@github.com/acme/Quill", "acme/Quill"),
            ("git@github.com:acme/Quill.git", "acme/Quill"),
            ("ssh://git@github.com/acme/Quill.git", "acme/Quill"),
            ("ssh://git@host:2222/a/b/Quill.git", "a/b/Quill"),
            ("https://gitlab.com/acme/backend/api.git", "acme/backend/api"),
            ("git@gitlab.com:other/backend/api.git", "other/backend/api"),
            ("https://dev.azure.com/org/proj/_git/repo", "org/proj/repo"),
            ("git@ssh.dev.azure.com:v3/org/proj/repo", "org/proj/repo"),
            ("D:/repos/owner/thing.git", "owner/thing"),
            ("/home/me/repos/owner/thing", "owner/thing"),
            ("file:///D:/repos/owner/thing.git", "owner/thing"),
        ):
            self.assertEqual(normalize_remote(url), slug, url)


class Ids(Base):
    def test_prefix_collision_and_case_insensitive_lookup(self):
        a = self.add("a", project="one/quill")
        b = self.add("b", project="two/quilting")
        c = self.add("c", project="three/qui")
        self.assertEqual([a, b, c], ["QUI-1", "QUI2-1", "QUI3-1"])
        self.assertEqual(self.add("d", project="one/quill"), "QUI-2")
        self.assertEqual(self.js("show", "qui2-1")["task"]["project"], "two/quilting")
        self.assertEqual(self.js("show", "Qui3-1")["task"]["title"], "c")
        self.js("show", "QUI-99", expect=5)

    def test_project_name_case_folds_to_first_spelling(self):
        self.add("a", project="acme/Quill")
        self.assertEqual(self.add("b", project="acme/quill"), "QUI-2")
        self.assertEqual(self.js("show", "QUI-2")["task"]["project"], "acme/Quill")

    def test_counters_are_per_project(self):
        self.add("a", project="x/alpha")
        self.add("b", project="x/alpha")
        self.assertEqual(self.add("c", project="x/beta"), "BET-1")


class Fields(Base):
    def test_defaults_and_set_fields(self):
        task = self.js("add", "t", "--project", self.project, agent="alice")["task"]
        self.assertEqual(
            (task["status"], task["kind"], task["priority"], task["visibility"], task["created_by"]),
            ("ready", "task", "P2", "local", "alice"),
        )
        updated = self.js(
            "set", task["id"], "--title", "new", "--pri", "p0", "--side", "BE", "--owner", "o",
            "--plan", "p.md", "--branch", "b", "--worktree", "w", "--pr", "#1", "--gh-issue", "#2",
            "--release-tag", "v1", "--visibility", "public", "--kind", "bug", "--add-tag", "x",
            "--add-tag", "y",
        )["task"]
        self.assertEqual(updated["priority"], "P0")
        self.assertEqual(updated["side"], "be")
        self.assertEqual(updated["tags"], ["x", "y"])
        self.assertEqual(updated["release_tag"], "v1")
        after = self.js("set", task["id"], "--remove-tag", "x", "--owner", "")["task"]
        self.assertEqual(after["tags"], ["y"])
        self.assertIsNone(after["owner"])
        self.js("set", task["id"], "--kind", "nonsense", expect=2)
        self.js("set", task["id"], expect=2)

    def test_ls_filters(self):
        a = self.js(
            "add", "fe bug", "--project", self.project, "--kind", "bug", "--pri", "P0", "--side", "fe",
            "--tag", "x", "--owner", "o",
        )["task"]["id"]
        b = self.add("be task", "--side", "be", "--parent", a)
        self.assertEqual(self.ls("ls", "--kind", "bug"), [a])
        self.assertEqual(self.ls("ls", "--pri", "P0"), [a])
        self.assertEqual(self.ls("ls", "--side", "be"), [b])
        self.assertEqual(self.ls("ls", "--tag", "x"), [a])
        self.assertEqual(self.ls("ls", "--owner", "o"), [a])
        self.assertEqual(self.ls("ls", "--parent", a), [b])
        self.assertEqual(self.ls("ls", "--limit", "1"), [a])
        self.js("claim", b, agent="zed")
        self.assertEqual(self.ls("ls", "--claimed-by", "zed"), [b])
        self.assertEqual(self.ls("ls", "--status", "in_progress", "--status", "ready"), [a, b])
        self.assertEqual(self.ls("ls", "--search", "fe"), [a])

    def test_ready_order_is_priority_then_age(self):
        low = self.add("low", "--pri", "P3")
        high = self.add("high", "--pri", "P0")
        first = self.add("first")
        second = self.add("second")
        self.assertEqual(self.ls("ready"), [high, first, second, low])

    def test_parent_cycle_refused(self):
        a = self.add("a")
        b = self.add("b", "--parent", a)
        self.js("set", a, "--parent", b, expect=2)
        self.assertEqual(self.js("set", a, "--parent", a, expect=2)["error"]["code"], "parent_cycle")

    def test_show_contains_everything(self):
        parent = self.add("parent")
        child = self.add("child", "--parent", parent)
        other = self.add("other")
        self.js("link", parent, "--relates", other)
        self.js("comment", parent, "note")
        data = self.js("show", parent.lower())
        self.assertEqual(set(data), {"ok", "task", "children", "comments", "links", "events"})
        self.assertEqual([c["id"] for c in data["children"]], [child])
        self.assertEqual(data["links"]["relates"], [other])
        self.assertEqual(data["comments"][0]["body"], "note")

    def test_report(self):
        waiting = self.add("waits")
        question = self.js("ask", waiting, "q?", "--to", "developer")["comment"]["id"]
        review = self.add("review", "--status", "needs_review")
        blocker, blocked = self.add("blocker"), self.add("blocked")
        self.js("link", blocker, "--blocks", blocked)
        stale = self.add("stale")
        self.js("claim", stale, agent="gone")
        self.sql("UPDATE tasks SET claim_expires_at = '2000-01-01T00:00:00Z' WHERE id = ?", stale)
        done = self.add("done")
        self.js("done", done, "--evidence", "e")
        data = self.js("report", "--project", self.project)
        self.assertEqual(data["tasks_left"], 5)
        self.assertEqual(data["counts_by_status"]["done"], 1)
        self.assertEqual(data["counts_by_priority"]["P2"], 5)
        self.assertEqual([t["id"] for t in data["stale_claims"]], [stale])
        self.assertEqual([t["id"] for t in data["blocked"]], [blocked])
        self.assertEqual([c["id"] for c in data["waiting_on_developer"]["questions"]], [question])
        self.assertEqual([t["id"] for t in data["waiting_on_developer"]["tasks"]], [review])

    def test_usage_errors_exit_2_and_json_flag_works_anywhere(self):
        self.assertEqual(self.run_qtask("nonsense").returncode, 2)
        self.assertEqual(self.run_qtask("claim").returncode, 2)
        self.assertEqual(json.loads(self.run_qtask("claim", "--json").stdout)["error"]["code"], "bad_usage")
        before = self.run_qtask("--json", "--project", self.project, "ls")
        self.assertEqual(json.loads(before.stdout)["ok"], True)

    def test_agent_from_environment(self):
        env = {**self.env, "QTASK_AGENT": "env-agent"}
        task = self.js("add", "x", "--project", self.project, env=env)["task"]
        self.assertEqual(task["created_by"], "env-agent")
        flagged = self.js("add", "y", "--project", self.project, "--agent", "flag", env=env)["task"]
        self.assertEqual(flagged["created_by"], "flag")

    def test_migration_is_idempotent_and_refuses_newer_schema(self):
        self.add()
        self.add()
        self.assertEqual(self.sql("SELECT version FROM schema_version"), [(1,), (2,)])
        self.assertEqual(self.sql("PRAGMA journal_mode")[0][0], "wal")
        self.sql("INSERT INTO schema_version(version, applied_at) VALUES (999, 'x')")
        self.assertEqual(self.js("ls", "--all", expect=1)["error"]["code"], "schema_too_new")


class EventsAndSearch(Base):
    def test_every_change_logs_an_event(self):
        task = self.js("add", "t", "--project", self.project, agent="alice")["task"]["id"]
        self.js("set", task, "--pri", "P0", "--add-tag", "x", agent="alice")
        self.js("claim", task, agent="alice")
        comment = self.js("comment", task, "note", agent="alice")["comment"]["id"]
        self.js("resolve", str(comment), agent="alice")
        other = self.add("other")
        self.js("link", task, "--blocks", other, agent="alice")
        self.js("unlink", task, "--blocks", other, agent="alice")
        self.js("release", task, agent="alice")
        self.js("done", task, "--evidence", "ran", agent="alice")
        events = self.js("show", task)["events"]
        fields = [e["field"] for e in events]
        for expected in (
            "created", "priority", "tags", "claimed_by", "claim_expires_at", "status",
            "comment", "comment_resolved", "link", "evidence", "closed_at",
        ):
            self.assertIn(expected, fields)
        self.assertEqual({e["actor"] for e in events}, {"alice"})
        change = next(e for e in events if e["field"] == "priority")
        self.assertEqual((change["old"], change["new"]), ("P2", "P0"))

    def test_refused_write_logs_nothing(self):
        task = self.add()
        count = len(self.js("show", task)["events"])
        self.js("done", task, expect=4)
        self.js("set", task, "--pri", "PX", expect=2)
        self.assertEqual(len(self.js("show", task)["events"]), count)

    @unittest.skipUnless(HAS_FTS5, "this Python's SQLite has no FTS5")
    def test_fts_finds_comment_text_and_stays_current(self):
        task = self.add("plain title")
        self.js("comment", task, "the xylophone timeout")
        data = self.js("search", "xylophone", "--project", self.project)
        self.assertEqual([c["task_id"] for c in data["comments"]], [task])
        self.assertEqual(self.ls("ls", "--search", "xylophone"), [task])
        self.assertEqual(self.sql("SELECT value FROM meta WHERE key = 'fts'"), [("1",)])
        self.assertEqual(self.js("search", "plain", "--project", self.project)["tasks"][0]["id"], task)
        self.js("set", task, "--title", "changed heading")
        self.assertEqual(self.js("search", "plain", "--project", self.project)["tasks"], [])
        self.assertEqual(self.js("search", "heading", "--project", self.project)["tasks"][0]["id"], task)
        self.js("search", 'odd "quote -not *', "--project", self.project)

    def test_search_respects_project_and_all(self):
        one = self.add("shared word", project="x/one")
        two = self.add("shared word", project="x/two")
        self.assertEqual([t["id"] for t in self.js("search", "shared", "--project", "x/one")["tasks"]], [one])
        self.assertEqual(
            sorted(t["id"] for t in self.js("search", "shared", "--all")["tasks"]), sorted([one, two])
        )

    def test_like_fallback_without_fts(self):
        env = {**self.env, "QTASK_NO_FTS": "1"}
        task = self.js("add", "plain title", "--project", self.project, env=env)["task"]["id"]
        self.js("comment", task, "the xylophone timeout", env=env)
        self.assertEqual(self.sql("SELECT value FROM meta WHERE key = 'fts'"), [("0",)])
        self.assertEqual(self.sql("SELECT count(*) FROM sqlite_master WHERE name = 'search_fts'"), [(0,)])
        data = self.js("search", "xylophone", "--project", self.project, env=env)
        self.assertEqual([c["task_id"] for c in data["comments"]], [task])

    def test_arabic_search(self):
        task = self.add(ARABIC)
        self.assertEqual(self.js("search", "الإرسال", "--project", self.project)["tasks"][0]["id"], task)


class Arabic(Base):
    def test_argument_file_stdin_json_and_text(self):
        body_file = Path(self.home, "body.md")
        body_file.write_text(ARABIC + "\nسطر ثان", encoding="utf-8")
        task = self.js(
            "add", ARABIC, "--project", self.project, "--body-file", str(body_file), "--tag", "عربي",
            "--agent", "وكيل",
        )["task"]
        self.assertEqual(task["title"], ARABIC)
        self.assertEqual(task["body"], ARABIC + "\nسطر ثان")
        self.assertEqual(task["tags"], ["عربي"])
        self.assertEqual(task["created_by"], "وكيل")
        self.assertEqual(self.sql("SELECT title FROM tasks"), [(ARABIC,)])
        self.assertEqual(self.ls("ls", "--tag", "عربي"), [task["id"]])
        text = self.run_qtask("show", task["id"])
        self.assertEqual(text.returncode, 0)
        self.assertIn(ARABIC, text.stdout)
        piped = self.run_qtask("comment", task["id"], "--body-file", "-", stdin=ARABIC)
        self.assertEqual(piped.returncode, 0, piped.stderr)
        self.assertEqual(self.js("show", task["id"])["comments"][0]["body"], ARABIC)

    def test_body_file_with_bom_and_crlf(self):
        path = Path(self.home, "bom.md")
        path.write_bytes("\ufeffسطر\r\nآخر".encode("utf-8"))
        task = self.js("add", "x", "--project", self.project, "--body-file", str(path))["task"]
        self.assertEqual(task["body"], "سطر\nآخر")

    def test_jsonl_export_is_utf8_with_lf(self):
        self.add(ARABIC)
        jsonl = Path(self.js("backup")["jsonl"])
        raw = jsonl.read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertIn(ARABIC, raw.decode("utf-8"))


class Backups(Base):
    def test_restore_has_identical_task_count(self):
        for i in range(5):
            self.add(f"task {i}")
        data = self.js("backup")
        conn = sqlite3.connect(data["db"])
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM tasks").fetchone()[0], 5)
            self.assertEqual(
                conn.execute("SELECT count(*) FROM events").fetchone()[0],
                self.sql("SELECT count(*) FROM events")[0][0],
            )
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            conn.close()
        lines = [json.loads(line) for line in Path(data["jsonl"]).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sum(1 for line in lines if line["table"] == "tasks"), 5)

    def test_restored_copy_works_as_a_home(self):
        task = self.add("kept")
        source = self.js("backup")["db"]
        restored = tempfile.mkdtemp(prefix="qtask-restored-")
        self.addCleanup(force_rmtree, restored)
        shutil.copy(source, Path(restored, "qtask.db"))
        env = {**self.env, "QTASK_HOME": restored}
        self.assertEqual(self.js("show", task, env=env)["task"]["title"], "kept")

    def test_rotation_keeps_seven_of_each(self):
        self.add()
        for _ in range(9):
            self.js("backup")
        folder = Path(self.home, "backups")
        self.assertEqual(len(list(folder.glob("qtask-*.db"))), 7)
        self.assertEqual(len(list(folder.glob("qtask-*.jsonl"))), 7)

    def test_auto_backup_on_first_write_of_the_day_only(self):
        folder = Path(self.home, "backups")
        self.js("ls", "--all")
        self.assertFalse(folder.exists())
        task = self.add("one")
        self.assertEqual(len(list(folder.glob("qtask-*.db"))), 1)
        self.add("two")
        self.assertEqual(len(list(folder.glob("qtask-*.db"))), 1)
        self.sql("UPDATE meta SET value = '2000-01-01' WHERE key = 'last_backup_day'")
        self.js("done", task, expect=4)
        self.assertEqual(len(list(folder.glob("qtask-*.db"))), 1)
        self.add("three")
        self.assertEqual(len(list(folder.glob("qtask-*.db"))), 2)


class Subtasks(Base):
    def family(self):
        parent = self.add("parent", "--pri", "P1")
        kids = [self.add(f"kid {i}", "--parent", parent) for i in range(3)]
        return parent, kids

    def test_tree_with_id_indents_and_shows_status_and_priority(self):
        parent, kids = self.family()
        self.js("done", kids[0], "--evidence", "ran")
        text = self.run_qtask("tree", parent).stdout.splitlines()
        self.assertEqual(len(text), 4)
        self.assertTrue(text[0].startswith(parent))
        self.assertIn("P1", text[0])
        self.assertIn("[1/3]", text[0])
        self.assertTrue(text[1].startswith("  " + kids[0]))
        self.assertIn("done", text[1])
        self.assertIn("P2", text[1])
        data = self.js("tree", parent.lower())
        self.assertEqual(data["count"], 1)
        node = data["tree"][0]
        self.assertEqual(node["id"], parent)
        self.assertEqual(node["progress"], "1/3")
        self.assertEqual([c["id"] for c in node["children"]], kids)
        self.assertEqual(node["children"][0]["children"], [])
        self.js("tree", "NOPE-1", expect=5)

    def test_tree_without_id_lists_open_top_level_parents(self):
        parent, _ = self.family()
        self.add("loner")
        closed = self.add("closed parent")
        closed_kid = self.add("closed kid", "--parent", closed)
        self.js("done", closed_kid, "--evidence", "e")
        self.js("done", closed, "--evidence", "e")
        data = self.js("tree", "--project", self.project)
        self.assertEqual([n["id"] for n in data["tree"]], [parent])
        self.assertEqual(len(data["tree"][0]["children"]), 3)
        other = self.add("other parent", project="x/other")
        self.add("other kid", "--parent", other, project="x/other")
        self.assertEqual(len(self.js("tree", "--all")["tree"]), 2)
        self.assertEqual(self.js("tree", "--project", "x/empty")["tree"], [])
        self.assertIn("No open parents", self.run_qtask("tree", "--project", "x/empty").stdout)

    def test_progress_ignores_dropped_children(self):
        parent, kids = self.family()
        self.js("done", kids[0], "--evidence", "e")
        self.js("drop", kids[1], "--reason", "no")
        data = self.js("show", parent)["task"]
        self.assertEqual((data["progress"], data["children_done"], data["children_total"]), ("1/2", 1, 2))
        self.assertIn("Progress: 1/2", self.run_qtask("show", parent).stdout)
        self.assertIn("[1/2]", self.run_qtask("ls", "--project", self.project).stdout)
        listed = self.js("ls", "--project", self.project)["tasks"]
        self.assertEqual(next(t for t in listed if t["id"] == parent)["progress"], "1/2")
        self.assertIsNone(next(t for t in listed if t["id"] == kids[2])["progress"])
        report = self.js("report", "--project", self.project)
        self.assertEqual([(t["id"], t["progress"]) for t in report["parents"]], [(parent, "1/2")])
        self.assertIn("[1/2]", self.run_qtask("report", "--project", self.project).stdout)

    def test_depth_stops_at_two_levels(self):
        parent, kids = self.family()
        error = self.js("add", "too deep", "--project", self.project, "--parent", kids[0], expect=2)["error"]
        self.assertEqual(error["code"], "parent_too_deep")
        loner = self.add("loner")
        self.assertEqual(self.js("set", loner, "--parent", kids[0], expect=2)["error"]["code"], "parent_too_deep")
        top = self.add("top")
        self.assertEqual(self.js("set", parent, "--parent", top, expect=2)["error"]["code"], "parent_too_deep")
        self.assertEqual(self.js("set", loner, "--parent", parent)["task"]["parent_id"], parent)

    def test_done_still_refuses_open_children(self):
        parent, kids = self.family()
        self.assertEqual(self.js("done", parent, "--evidence", "e", expect=4)["error"]["open_children"], kids)


class Plans(Base):
    project = "acme/Quill"

    def plan(self, topic, *extra, project=None):
        return self.js("plan-path", topic, "--project", project or self.project, *extra)

    def test_path_without_task_uses_short_project_and_topic(self):
        data = self.plan("Retry Flow")
        path = Path(data["path"])
        self.assertTrue(data["created"])
        self.assertEqual(path.name, "retry-flow.md")
        self.assertEqual(path.parent.parent, Path(self.home, "plans", "quill"))
        self.assertIsNone(data["task_id"])
        raw = path.read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertTrue(raw.decode("utf-8").startswith("# retry flow\n"))
        self.js("plan-path", "../evil", "--project", self.project, expect=2)
        self.js("plan-path", "nul", "--project", self.project, expect=2)
        self.js("plan-path", "x", "--all", expect=2)

    def test_task_fills_template_and_sets_plan_path(self):
        task = self.add(ARABIC)
        outside = tempfile.mkdtemp(prefix="qtask-outside-")
        self.addCleanup(force_rmtree, outside)
        data = self.js("plan-path", "retry", "--task", task.lower(), cwd=outside)
        path = Path(data["path"])
        self.assertEqual(path.name, f"{task}-retry.md")
        self.assertEqual(path.parent.parent, Path(self.home, "plans", "quill"))
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(f"# {ARABIC}\n"))
        self.assertIn(f"**Task:** {task}\n", text)
        self.assertTrue(data["plan_path_set"])
        self.assertEqual(self.js("show", task)["task"]["plan_path"], data["path"])
        self.assertIn("plan_path", [e["field"] for e in self.js("show", task)["events"]])

    def test_task_with_a_plan_path_keeps_it_and_existing_file_survives(self):
        task = self.add("t", "--plan", "D:/repo/plan.md")
        data = self.js("plan-path", "retry", "--task", task)
        self.assertFalse(data["plan_path_set"])
        self.assertEqual(self.js("show", task)["task"]["plan_path"], "D:/repo/plan.md")
        Path(data["path"]).write_text("# kept", encoding="utf-8")
        again = self.js("plan-path", "retry", "--task", task)
        self.assertFalse(again["created"])
        self.assertEqual(Path(data["path"]).read_text(encoding="utf-8"), "# kept")

    def test_two_projects_sharing_a_repo_name(self):
        one = Path(self.plan("x", project="one/Quill")["path"])
        two = Path(self.plan("x", project="two/quill")["path"])
        self.assertEqual(one.parent.parent.name, "quill")
        self.assertEqual(two.parent.parent.name, "quill2")
        self.assertEqual(Path(self.plan("y", project="one/Quill")["path"]).parent.parent.name, "quill")
        self.assertEqual(self.add("t", project="three/quill"), "QUI3-1")
        self.assertEqual(Path(self.plan("x", project="three/quill")["path"]).parent.parent.name, "quill3")

    def test_migration_from_schema_one_keeps_tasks_and_older_plan_files(self):
        sys.path.insert(0, str(ROOT))
        try:
            import qtask
        finally:
            sys.path.pop(0)
        conn = sqlite3.connect(Path(self.home, "qtask.db"), isolation_level=None)
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        qtask.migrate_1(conn)
        conn.execute("INSERT INTO schema_version VALUES (1, 'x')")
        for name, prefix, at in (("two/quill", "QUI2", "2026-02"), ("acme/Quill", "QUI", "2026-01")):
            conn.execute("INSERT INTO projects(name, prefix, next_n, created_at) VALUES (?, ?, 1, ?)", (name, prefix, at))
        conn.close()
        old_file = Path(self.home, "plans", "acme", "Quill", "2026-10-01", "old-plan.md")
        old_file.parent.mkdir(parents=True)
        old_file.write_text("# Old plan\n", encoding="utf-8")
        new_path = Path(self.plan("new")["path"])
        self.assertEqual(new_path.parent.parent.name, "quill")
        self.assertEqual(self.sql("SELECT max(version) FROM schema_version"), [(2,)])
        self.assertEqual(
            sorted(self.sql("SELECT name, short_name FROM projects")),
            [("acme/Quill", "quill"), ("two/quill", "quill2")],
        )
        self.assertEqual(self.add("first after migration"), "QUI-1")
        listed = self.js("plans", "--project", self.project)["plans"]
        self.assertEqual({p["title"] for p in listed}, {"new", "Old plan"})
        self.assertTrue(old_file.exists())

    def seeded(self):
        task = self.add("Linked title")
        plan = self.js("plan-path", "alpha", "--task", task)
        free = self.plan("beta")
        old = Path(self.home, "plans", "quill", "2020-05-05", "gamma.md")
        old.parent.mkdir(parents=True)
        old.write_text("```\n# not a heading\n```\n## Real heading\nneedle-word here\n", encoding="utf-8")
        return task, plan["path"], free["path"], old

    def test_plans_newest_first_with_link_and_title(self):
        task, linked, free, old = self.seeded()
        data = self.js("plans", "--project", self.project)
        self.assertEqual(data["count"], 3)
        self.assertEqual(set(data["plans"][0]), {"date", "project", "task_id", "status", "title", "path"})
        self.assertEqual(data["plans"][-1]["title"], "Real heading")
        self.assertEqual(data["plans"][-1]["date"], "2020-05-05")
        self.assertIsNone(data["plans"][-1]["task_id"])
        entry = next(p for p in data["plans"] if p["path"] == linked)
        self.assertEqual((entry["task_id"], entry["status"], entry["title"]), (task, "ready", "Linked title"))
        self.assertGreaterEqual(data["plans"][0]["date"], data["plans"][1]["date"])
        lines = self.run_qtask("plans", "--project", self.project).stdout.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn(f"{task} ready", next(l for l in lines if linked in l))
        self.assertTrue(lines[-1].startswith("2020-05-05  -  Real heading  "))
        self.js("claim", task, agent="a")
        status = next(p for p in self.js("plans", "--project", self.project)["plans"] if p["path"] == linked)
        self.assertEqual(status["status"], "in_progress")

    def test_plans_day_and_search(self):
        task, linked, free, old = self.seeded()
        day = self.js("plans", "--project", self.project, "--day", "2020-05-05")["plans"]
        self.assertEqual([p["title"] for p in day], ["Real heading"])
        self.assertEqual(self.js("plans", "--project", self.project, "--day", "1999-01-01")["count"], 0)
        self.js("plans", "--project", self.project, "--day", "not-a-date", expect=2)
        for text, title in (("NEEDLE-WORD", "Real heading"), ("linked TITLE", "Linked title"), (task, "Linked title")):
            found = self.js("plans", "--project", self.project, "--search", text)["plans"]
            self.assertEqual([p["title"] for p in found], [title], text)

    def test_plans_link_through_plan_path_and_older_folder(self):
        old = Path(self.home, "plans", "acme", "Quill", "2026-10-01", "older.md")
        old.parent.mkdir(parents=True)
        old.write_text("no heading", encoding="utf-8")
        task = self.add("t", "--plan", str(old))
        entry = self.js("plans", "--project", self.project)["plans"][0]
        self.assertEqual((entry["task_id"], entry["title"]), (task, "older"))

    def test_plans_scope_and_all(self):
        self.plan("mine")
        self.plan("theirs", project="x/other")
        stray = Path(self.home, "plans", "unknown-folder", "2026-01-01", "stray.md")
        stray.parent.mkdir(parents=True)
        stray.write_text("# Stray", encoding="utf-8")
        self.assertEqual([p["title"] for p in self.js("plans", "--project", self.project)["plans"]], ["mine"])
        self.assertEqual({p["title"] for p in self.js("plans", "--all")["plans"]}, {"mine", "theirs", "Stray"})
        outside = tempfile.mkdtemp(prefix="qtask-outside-")
        self.addCleanup(force_rmtree, outside)
        self.js("plans", cwd=outside, expect=2)
        self.assertEqual(self.js("plans", "--project", "x/nothing")["count"], 0)

    def test_plan_id_in_another_project_does_not_link(self):
        foreign = self.add("foreign", project="x/alpha")
        self.plan("mine")
        wrong = Path(self.home, "plans", "quill", "2026-01-01", f"{foreign}-note.md")
        wrong.parent.mkdir(parents=True)
        wrong.write_text("# Note", encoding="utf-8")
        entry = next(p for p in self.js("plans", "--project", self.project)["plans"] if p["title"] == "Note")
        self.assertIsNone(entry["task_id"])


class LauncherHelpers(Base):
    def title_body_plan_agent(self):
        rows = self.sql("SELECT title, body, plan_path, created_by FROM tasks ORDER BY rowid DESC LIMIT 1")
        return rows[0]


@unittest.skipUnless(BASH, "bash is not installed")
class ShellLauncher(LauncherHelpers):
    """launchers/qtask, run by bash: Git Bash on Windows, bash elsewhere."""

    def bash(self, script):
        path = Path(self.home, "run.sh")
        path.write_text(script, encoding="utf-8", newline="\n")
        return subprocess.run([BASH, str(path)], capture_output=True, encoding="utf-8", cwd=self.home, env=self.env)

    def launcher(self):
        return (LAUNCHERS / "qtask").as_posix()

    def test_text_and_paths_arrive_unchanged(self):
        result = self.bash(
            "export QTASK_AGENT=bash-agent\n"
            f"sh '{self.launcher()}' add '{ARABIC}' --project acme/app --body 'say \"hi\" & don'\\''t' "
            "--plan /d/GitHub/x.md --json\n"
            "echo rc=$?\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rc=0", result.stdout, result.stderr)
        self.assertIn(ARABIC, result.stdout)
        self.assertEqual(
            self.title_body_plan_agent(), (ARABIC, "say \"hi\" & don't", "/d/GitHub/x.md", "bash-agent")
        )

    def test_exit_code_and_flag_value_with_slash(self):
        task = self.add()
        self.js("claim", task, agent="alice")
        result = self.bash(
            f"sh '{self.launcher()}' claim {task} --agent bob; echo rc=$?\n"
            f"sh '{self.launcher()}' set {task} --worktree /tmp/x --plan=/c/Users/x.md --agent alice; echo rc=$?\n"
        )
        self.assertIn("rc=3", result.stdout, result.stderr)
        self.assertIn("rc=0", result.stdout, result.stderr)
        data = self.js("show", task)["task"]
        self.assertEqual((data["worktree"], data["plan_path"]), ("/tmp/x", "/c/Users/x.md"))


@unittest.skipUnless(WINDOWS, "launchers/qtask.cmd is for Windows")
class CmdLauncher(LauncherHelpers):
    """launchers/qtask.cmd, called from PowerShell 5.1."""

    def powershell(self, script):
        prefix = "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
        encoded = base64.b64encode((prefix + script).encode("utf-16-le")).decode("ascii")
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-EncodedCommand", encoded],
            capture_output=True, encoding="utf-8", cwd=self.home, env=self.env,
        )

    def launcher(self):
        return str(LAUNCHERS / "qtask.cmd")

    def test_text_and_quotes(self):
        result = self.powershell(
            "$env:QTASK_AGENT = 'ps-agent'\n"
            f"& '{self.launcher()}' add '{ARABIC}' --project acme/app --body 'say \\\"hi\\\" now' "
            "--plan /d/GitHub/x.md --json\n"
            "\"rc=$LASTEXITCODE\"\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rc=0", result.stdout, result.stderr)
        self.assertIn(ARABIC, result.stdout)
        self.assertEqual(
            self.title_body_plan_agent(), (ARABIC, 'say "hi" now', "/d/GitHub/x.md", "ps-agent")
        )

    def test_exit_code_and_body_file_with_quotes(self):
        path = Path(self.home, "body.md")
        path.write_text('he said "مرحبا" & left 100%', encoding="utf-8")
        task = self.add()
        self.js("claim", task, agent="alice")
        result = self.powershell(
            f"& '{self.launcher()}' claim {task} --agent bob\n"
            "\"rc=$LASTEXITCODE\"\n"
            f"& '{self.launcher()}' set {task} --body-file '{path}' --agent alice\n"
            "\"rc=$LASTEXITCODE\"\n"
        )
        self.assertIn("rc=3", result.stdout, result.stderr)
        self.assertIn("rc=0", result.stdout, result.stderr)
        self.assertEqual(self.js("show", task)["task"]["body"], 'he said "مرحبا" & left 100%')


class Hardening(Base):
    """Fixes from the review before the first public release."""

    def import_qtask(self):
        sys.path.insert(0, str(ROOT))
        try:
            import qtask
        finally:
            sys.path.pop(0)
        return qtask

    def test_git_comes_from_an_absolute_path_entry_not_the_current_folder(self):
        qtask = self.import_qtask()
        name = "git.exe" if WINDOWS else "git"
        planted = Path(self.home, "repo")
        real = Path(self.home, "bin")
        for folder in (planted, real):
            folder.mkdir()
            (folder / name).write_bytes(b"")
            os.chmod(folder / name, 0o755)
        old_cwd, old_path = os.getcwd(), os.environ.get("PATH", "")
        try:
            os.chdir(planted)
            os.environ["PATH"] = os.pathsep.join([".", "repo", "", str(real)])
            qtask.git_path.cache_clear()
            self.assertEqual(qtask.git_path(), os.path.join(str(real), name))
        finally:
            os.chdir(old_cwd)
            os.environ["PATH"] = old_path
            qtask.git_path.cache_clear()

    def test_no_git_says_so(self):
        env = {**self.env, "PATH": ""}
        outside = tempfile.mkdtemp(prefix="qtask-outside-")
        self.addCleanup(force_rmtree, outside)
        error = self.js("ls", cwd=outside, env=env, expect=2)["error"]
        self.assertEqual(error["code"], "project_required")
        self.assertIn("git was not found", error["message"])

    def test_search_past_the_sqlite_parameter_limit(self):
        task = self.add("haystack")
        self.js("comment", task, "needle 0")
        rows = [(task, "tester", f"needle {n}", "note", "2026-01-01T00:00:00Z") for n in range(1, 33_000)]
        conn = sqlite3.connect(Path(self.home) / "qtask.db", timeout=15)
        try:
            columns = [r[1] for r in conn.execute("PRAGMA table_info(comments)")]
            names = [c for c in ("task_id", "author", "body", "kind", "created_at") if c in columns]
            self.assertEqual(len(names), 5, columns)
            with conn:
                conn.executemany(f"INSERT INTO comments({', '.join(names)}) VALUES (?, ?, ?, ?, ?)", rows)
        finally:
            conn.close()
        self.assertEqual(len(self.js("search", "needle", "--project", self.project)["comments"]), 33_000)
        self.assertEqual(self.ls("ls", "--search", "needle"), [task])

    def test_bad_ttl_is_a_usage_error(self):
        task = self.add()
        for value in ("nan", "inf", "1e9", "0", "-1"):
            self.assertEqual(self.js("claim", task, "--ttl-hours", value, agent="a", expect=2)["error"]["code"], "bad_usage")

    def test_long_topic_is_a_usage_error(self):
        self.js("plan-path", "x" * 121, "--project", self.project, expect=2)
        self.js("plan-path", "x" * 120, "--project", self.project)

    def test_relative_home_is_refused(self):
        env = {**self.env, "QTASK_HOME": "relative-home"}
        error = self.js("ls", "--project", self.project, env=env, cwd=self.home, expect=2)["error"]
        self.assertEqual(error["code"], "bad_home")
        self.assertFalse(Path(self.home, "relative-home").exists())

    def test_home_that_is_a_file_gives_json_not_a_traceback(self):
        path = Path(self.home, "a-file")
        path.write_text("x", encoding="utf-8")
        result = self.run_qtask("ls", "--project", self.project, "--json", env={**self.env, "QTASK_HOME": str(path)})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "os_error")

    @unittest.skipIf(WINDOWS, "file modes are a POSIX matter")
    def test_data_is_private_to_the_user(self):
        home = Path(self.home, "fresh")
        self.js("add", "x", "--project", self.project, env={**self.env, "QTASK_HOME": str(home)})
        self.assertEqual(stat.S_IMODE(home.stat().st_mode) & 0o077, 0)
        self.assertEqual(stat.S_IMODE((home / "qtask.db").stat().st_mode) & 0o077, 0)


class Version(Base):
    def test_version_matches_the_package(self):
        result = self.run_qtask("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        version = result.stdout.strip().removeprefix("qtask ")
        self.assertIn(f'version = "{version}"', project)


if __name__ == "__main__":
    unittest.main(verbosity=1)
