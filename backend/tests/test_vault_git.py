"""Vault git writer: advisory lock, stale index.lock recovery, signal handling. Uses a real temp repository."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from jobpilot.projection import vault as vault_module
from jobpilot.projection.vault import (
    GIT_LOCK_KEY,
    GIT_LOCK_NAMESPACE,
    GitError,
    _run,
    commit_cv,
    ensure_vault,
    recover_stale_index_lock,
)

HAVE_GIT = shutil.which("git") is not None


class FakeDb:
    """Records advisory-lock use; the real lock is exercised by check_dispatch scenario 35."""

    def __init__(self) -> None:
        self.held = False
        self.calls: list[tuple[str, int]] = []
        self.overlap = False

    @contextmanager
    def session_lock(self, key: str, namespace: int) -> Any:
        self.overlap = self.overlap or self.held
        self.held = True
        self.calls.append((key, namespace))
        try:
            yield
        finally:
            self.held = False


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class VaultGitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.vault = self.dir / "vault"
        ensure_vault(self.vault)
        (self.vault / "_cv-store").mkdir(exist_ok=True)
        self.db: Any = FakeDb()

    def write(self, name: str, text: str) -> str:
        path = self.vault / "_cv-store" / name
        path.write_text(text)
        return f"_cv-store/{name}"

    def stale(self, age: float) -> Path:
        lock = self.vault / ".git" / "index.lock"
        lock.write_text("")
        old = time.time() - age
        os.utime(lock, (old, old))
        return lock

    def test_commit_runs_under_the_advisory_lock_and_returns_the_sha(self) -> None:
        sha = commit_cv(self.vault, "cv: one", [self.write("a.tex", "a")], db=self.db)
        self.assertEqual(len(sha or ""), 40)
        self.assertEqual(self.db.calls, [(GIT_LOCK_KEY, GIT_LOCK_NAMESPACE)])
        self.assertFalse(self.db.held)

    def test_an_unchanged_path_commits_nothing(self) -> None:
        path = self.write("a.tex", "a")
        commit_cv(self.vault, "cv: one", [path], db=self.db)
        self.assertIsNone(commit_cv(self.vault, "cv: again", [path], db=self.db))

    def test_no_paths_takes_no_lock(self) -> None:
        self.assertIsNone(commit_cv(self.vault, "x", [], db=self.db))
        self.assertEqual(self.db.calls, [])

    def test_a_stale_index_lock_is_moved_aside_not_deleted_and_the_commit_succeeds(self) -> None:
        lock = self.stale(600)
        moved: list[Path] = []
        sha = commit_cv(self.vault, "cv: one", [self.write("a.tex", "a")], db=self.db, on_recovered=moved.append)
        self.assertTrue(sha)
        self.assertFalse(lock.exists())
        self.assertEqual(len(moved), 1)
        self.assertTrue(moved[0].exists() and moved[0].name.startswith("index.lock.stale-"))

    def test_a_recent_index_lock_is_left_alone_and_the_commit_fails_loudly(self) -> None:
        lock = self.stale(5)
        with self.assertRaises(GitError) as raised:
            commit_cv(self.vault, "cv: one", [self.write("a.tex", "a")], db=self.db, on_recovered=MagicMock())
        self.assertIn("index.lock", str(raised.exception))
        self.assertTrue(lock.exists())

    def test_two_stale_locks_get_distinct_names(self) -> None:
        for _ in range(2):
            self.stale(600)
            self.assertIsNotNone(recover_stale_index_lock(self.vault))
        self.assertEqual(len(list((self.vault / ".git").glob("index.lock.stale-*"))), 2)

    def test_no_lock_means_nothing_to_recover(self) -> None:
        self.assertIsNone(recover_stale_index_lock(self.vault))

    def test_recovery_never_touches_repository_data(self) -> None:
        sha = commit_cv(self.vault, "cv: one", [self.write("a.tex", "a")], db=self.db)
        self.stale(600)
        recover_stale_index_lock(self.vault)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.vault, capture_output=True, text=True).stdout.strip()
        self.assertEqual(head, sha)

    def test_the_lock_is_held_for_the_whole_critical_section(self) -> None:
        seen: list[bool] = []
        original = vault_module._run

        def spy(command: list[str], cwd: Path, timeout: float = 1.0) -> str:
            seen.append(self.db.held)
            return original(command, cwd)

        vault_module._run = spy  # type: ignore[assignment]
        self.addCleanup(setattr, vault_module, "_run", original)
        commit_cv(self.vault, "cv: one", [self.write("a.tex", "a")], db=self.db)
        self.assertTrue(seen and all(seen))


class RunTests(unittest.TestCase):
    def test_a_timed_out_command_gets_sigterm_first_and_the_error_surfaces(self) -> None:
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            _run(["sleep", "30"], Path.cwd(), timeout=0.2)
        self.assertLess(time.monotonic() - started, 5)  # sleep dies on SIGTERM; no 10 s grace wait, no SIGKILL

    def test_a_failing_command_raises_with_its_stderr(self) -> None:
        with self.assertRaises(GitError) as raised:
            _run(["git", "definitely-not-a-command"], Path.cwd())
        self.assertIn("definitely-not-a-command", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
