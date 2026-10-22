from __future__ import annotations

import fcntl
import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..domain.canonical import slug
from ..repository.database import Database

NOTES_MARKER = "<!-- notes: everything below is yours and is never overwritten -->"


@dataclass
class JobNote:
    id: str
    company: str
    role: str
    url: str
    board: str | None
    status: str
    waiting_on: str
    blocked_reason: str | None
    cv_track: str | None
    cv_mode: str
    cv_sha: str | None
    match_score: int | None
    location: str | None
    work_mode: str | None
    pay: str | None
    deadline: str | None
    applied_on: str | None
    applied_at: str | None
    confirmation: str | None
    updated_at: str
    posting: str | None


def safe_name(s: str, max_len: int = 60) -> str:
    v = re.sub(r"\s+", " ", re.sub(r"[\\/:*?\"<>|#^\[\]\n\r\t]+", " ", s)).strip(" .")[:max_len].strip(" .")
    return v or slug(s)


_projection_lock = threading.RLock()


@contextmanager
def _vault_lock(vault: Path) -> Iterator[None]:
    vault.mkdir(parents=True, exist_ok=True)
    with _projection_lock, (vault / ".projection.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def job_dir_for(vault: Path, company: str, role: str, job_id: str) -> Path:
    with _vault_lock(vault):
        return _job_dir_for(vault, company, role, job_id)


def _job_dir_for(vault: Path, company: str, role: str, job_id: str) -> Path:
    root = vault / "Jobs" / "Companies"
    target = root / safe_name(company) / f"{safe_name(role, 70)} - {job_id}"
    if not target.resolve().is_relative_to(vault.resolve()):
        raise ValueError("job folder outside vault")
    existing = [p for p in root.glob("*/*") if p.name.endswith(f" - {job_id}") and p.is_dir()]
    if len(existing) > 1:
        raise ValueError(f"multiple existing vault folders for {job_id}; resolve manually")
    if existing and (existing[0].is_symlink() or not existing[0].resolve().is_relative_to(vault.resolve())):
        raise ValueError("existing job folder escapes vault")
    if existing and existing[0] != target:
        source = existing[0]
        # Relative CV links must still resolve after moving across company folders.
        links = {p.name: p.resolve() for p in source.iterdir() if p.is_symlink()}
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
        for name, destination in links.items():
            link = target / name
            replacement = target / f".{name}.{os.getpid()}.tmp"
            replacement.symlink_to(os.path.relpath(destination, target.resolve()))
            os.replace(replacement, link)
    return target


def _y(v: str | int | None) -> str:
    if v is None or v == "":
        return "null"
    if isinstance(v, int):
        return str(v)
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def render_frontmatter(n: JobNote) -> str:
    rows: list[tuple[str, str | int | None]] = [
        ("type", "job"),
        ("id", n.id),
        ("company", n.company),
        ("role", n.role),
        ("url", n.url),
        ("board", n.board),
        ("status", n.status),
        ("waiting_on", n.waiting_on),
        ("blocked_reason", n.blocked_reason),
        ("cv_track", n.cv_track),
        ("cv_mode", n.cv_mode),
        ("cv_sha", n.cv_sha),
        ("match_score", n.match_score),
        ("location", n.location),
        ("work_mode", n.work_mode),
        ("pay", n.pay),
        ("deadline", n.deadline),
        ("applied_on", n.applied_on),
        ("applied_at", n.applied_at),
        ("confirmation", n.confirmation),
        ("updated", n.updated_at),
    ]
    return "---\n" + "\n".join(f"{k}: {_y(v)}" for k, v in rows) + "\n---\n"


def _atomic_write(path: Path, data: str) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(data)
    os.replace(tmp, path)


def write_job_note(vault: Path, n: JobNote) -> Path:
    with _vault_lock(vault):
        return _write_job_note(vault, n)


def _write_job_note(vault: Path, n: JobNote) -> Path:
    """Frontmatter + posting snapshot are generated; text after the notes marker is preserved."""
    d = _job_dir_for(vault, n.company, n.role, n.id)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "Job.md"
    user_notes = "\n"
    if path.exists():
        cur = path.read_text()
        i = cur.rfind(NOTES_MARKER)
        if i >= 0:
            user_notes = cur[i + len(NOTES_MARKER) :]
    text = (n.posting or "").replace(NOTES_MARKER, "").replace("<!-- notes -->", "").strip()
    fence = "`" * max(3, 1 + max((len(m.group(0)) for m in re.finditer(r"`+", text)), default=0))
    posting = f"\n## Posting snapshot\n\n{fence}text\n{text}\n{fence}\n" if text else ""
    body = f"{render_frontmatter(n)}\n# {n.role} — {n.company}\n\n[Open posting]({n.url})\n{posting}\n{NOTES_MARKER}{user_notes}"
    _atomic_write(path, body)
    return d


_BASES = {
    "Jobs.base": 'filters:\n  and:\n    - file.ext == "md"\n    - type == "job"\nviews:\n  - type: table\n    name: All jobs\n    order: [company, role, status, waiting_on, board, cv_track, cv_mode, applied_on, location, pay, deadline, url]\n    sort:\n      - property: updated\n        direction: DESC\n',
    "Applied.base": 'filters:\n  and:\n    - file.ext == "md"\n    - type == "job"\n    - applied_on != null\nviews:\n  - type: table\n    name: Applied\n    order: [applied_on, company, role, cv_track, cv_sha, confirmation, board, url]\n    sort:\n      - property: applied_on\n        direction: DESC\n',
    "Needs-you.base": 'filters:\n  and:\n    - file.ext == "md"\n    - type == "job"\n    - waiting_on == "you"\nviews:\n  - type: table\n    name: Needs you\n    order: [company, role, status, blocked_reason, board, url]\n',
}


def ensure_vault(vault: Path) -> None:
    for d in ("Jobs/Companies", "CV Tracks", "_cv-store", "Profile"):
        (vault / d).mkdir(parents=True, exist_ok=True)
    for name, body in _BASES.items():
        p = vault / name
        if not p.exists():
            p.write_text(body)
    if not (vault / ".git").exists():
        _run(["git", "init", "-q", "-b", "main"], vault)
        (vault / ".gitignore").write_text(".DS_Store\n.obsidian/workspace*\n*.tmp\n")


# Every vault git write is serialized across threads, processes and containers by one PostgreSQL advisory lock: the
# vault is a bind mount shared by server, worker and control, where flock/threading locks do not reach each other.
GIT_LOCK_KEY = "vault-git"
GIT_LOCK_NAMESPACE = 4
STALE_LOCK_SECONDS = 120  # an index.lock older than this, found while holding the advisory lock, is a crash leftover
GIT_TIMEOUT_SECONDS = 120.0
TERM_GRACE_SECONDS = 10.0  # SIGTERM first: git removes its own lock files on it; SIGKILL is what leaves them behind

log = logging.getLogger("jobpilot.vault")


class GitError(RuntimeError):
    """A git command failed. The message is git's own stderr (paths and refs, never request data)."""


def _run(command: list[str], cwd: Path, timeout: float = GIT_TIMEOUT_SECONDS) -> str:
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.communicate(timeout=TERM_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
        raise
    if process.returncode:
        raise GitError(f"{' '.join(command[:2])} failed ({process.returncode}): {err.strip()[:400]}")
    return out


def recover_stale_index_lock(vault: Path, *, now: float | None = None, max_age: float = STALE_LOCK_SECONDS) -> Path | None:
    """Move aside (never delete) a `.git/index.lock` left by a git process that died. Call only while holding the
    advisory lock, so no JobPilot git is running; the age threshold covers git run by anything else. Returns the new
    path, or None when there was no lock or it is recent."""
    lock = vault / ".git" / "index.lock"
    try:
        age = (time.time() if now is None else now) - lock.stat().st_mtime
    except FileNotFoundError:
        return None
    if age < max_age:
        return None
    stamp = datetime.fromtimestamp(lock.stat().st_mtime, UTC).strftime("%Y%m%dT%H%M%SZ")
    target = lock.with_name(f"index.lock.stale-{stamp}")
    n = 0
    while target.exists():
        n += 1
        target = lock.with_name(f"index.lock.stale-{stamp}-{n}")
    lock.rename(target)
    log.warning("moved aside a stale git index.lock (%ds old) to %s", int(age), target.name)
    return target


def commit_cv(
    vault: Path,
    message: str,
    paths: list[str],
    *,
    db: Database,
    on_recovered: Callable[[Path], None] | None = None,
) -> str | None:
    """The one writer of vault git history. Holds the advisory lock around recovery, add and commit."""
    if not paths:
        return None
    with db.session_lock(GIT_LOCK_KEY, GIT_LOCK_NAMESPACE), _vault_lock(vault):
        recovered = recover_stale_index_lock(vault)
        if recovered is not None and on_recovered is not None:
            on_recovered(recovered)
        _run(["git", "add", "-A", "--", *paths], vault)
        if not _run(["git", "status", "--porcelain", "--", *paths], vault).strip():
            return None
        _run(
            [
                "git",
                "-c",
                "user.name=JobPilot",
                "-c",
                "user.email=jobpilot@localhost",
                "commit",
                "-q",
                "-m",
                message,
                "--",
                *paths,
            ],
            vault,
        )
        return _run(["git", "rev-parse", "HEAD"], vault).strip()


def cv_history(vault: Path, path: str, limit: int = 20) -> list[dict[str, str]]:
    r = subprocess.run(
        ["git", "log", f"-{limit}", "--format=%H%x09%cI%x09%s", "--", path], cwd=vault, capture_output=True, text=True
    )
    out = []
    for line in r.stdout.strip().splitlines():
        sha, date, subject = (line.split("\t") + ["", ""])[:3]
        out.append({"sha": sha, "date": date, "subject": subject})
    return out
