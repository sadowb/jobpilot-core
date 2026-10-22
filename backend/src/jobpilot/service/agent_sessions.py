"""Private Claude transcripts, stable cwd, delta DATA and bounded retention."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from ..domain.ids import agent_session_id
from ..dto.direct import ResultInvalid, data_block, parse_result, safe_data
from ..repository.agent_sessions import AgentSessionRepository

if TYPE_CHECKING:
    from .application import Services

log = logging.getLogger(__name__)
RETENTION = 14 * 86400
JOB_NAME = re.compile(r"j_[a-z2-7]+")


def delta_prompt(instruction: str, previous: dict[str, str]) -> tuple[str, dict[str, str]]:
    """Compare individual owner values by hash; keep current tab/draft context explicit on every turn."""
    marker = "DATA JSON ("
    if marker not in instruction:
        return instruction, {}  # internal tests/legacy callers; production always uses data_block
    prefix, block = instruction.split(marker, 1)
    payload, suffix = block.split("\n", 1)[1].rsplit("\nEND DATA", 1)
    data = json.loads(payload)
    hashes: dict[str, str] = {}
    delta: dict[str, Any] = {}
    for key, value in data.items():
        if key in ("profile", "answers", "facts"):
            # Always sent in full. A resumed Haiku turn does not reliably look back through hundreds of earlier messages
            # (it reported "no address supplied" while the first turn had it), and these three are what every form needs.
            delta[key] = value
        else:
            hashes[key] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
            if previous.get(key) != hashes[key] or key in (
                "job_id",
                "target_id",
                "cv_path",
            ):
                delta[key] = value
    return (
        prefix
        + "Continue this conversation using the following DATA delta; omitted values are unchanged. The profile, answers and facts below are always the complete current set. "
        "Replacement collections supersede earlier material. The agent never submits: the owner does.\n"
        + data_block(delta)
        + suffix,
        hashes,
    )


class AgentSessions:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.repo = AgentSessionRepository(svc.db)
        self.root = svc.cfg.data_dir / "agent-sessions"
        self._scrubbed: dict[Path, tuple[int, int]] = {}

    def directory(self, job_id: str) -> Path:
        if not JOB_NAME.fullmatch(job_id):
            raise ValueError("invalid session job id")
        root = self.root
        if root.is_symlink():
            raise ValueError("session root must not be a symlink")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        path = root / job_id
        if path.is_symlink():
            raise ValueError("session directory must not be a symlink")
        path.mkdir(exist_ok=True, mode=0o700)
        path.chmod(0o700)
        return path

    @staticmethod
    def valid(directory: Path, session_id: str) -> bool:
        try:
            UUID(session_id)
            # CLI stores transcripts below projects/<encoded cwd>/<uuid>.jsonl. Discover the project component
            # rather than duplicating the CLI's cwd encoding, which can change between CLI releases.
            files = list(directory.glob(f"projects/*/{session_id}.jsonl"))
            if (
                len(files) != 1
                or any(p.is_symlink() for p in (files[0], files[0].parent, directory / "projects"))
                or files[0].stat().st_size > 32_000_000
            ):
                return False
            conversation = False
            for line in files[0].read_text().splitlines():
                row = json.loads(line)
                if not isinstance(row, dict) or row.get("sessionId", session_id) != session_id:
                    return False
                conversation |= row.get("type") in ("user", "assistant")
            return conversation
        except (ValueError, OSError):
            return False

    def start(self, job_id: str) -> tuple[str, bool, dict[str, str]]:
        directory = self.directory(job_id)
        row = self.repo.get(job_id)
        if row and row["id"]:
            # A killed worker may leave raw CLI output behind. Scrub it before the next process can resume.
            self.sanitize(job_id)
            if self.valid(directory, str(row["id"])):
                return str(row["id"]), True, row["context"] or {}
            return self.reset(job_id, "missing_or_corrupt"), False, {}
        row = self.repo.create(job_id, agent_session_id())
        return str(row["id"]), False, {}

    def reset(self, job_id: str, reason: str) -> str:
        self.remove(job_id)
        session_id = agent_session_id()
        with self.svc.db.transaction():
            self.repo.reset(job_id, session_id)
            self.svc.event(job_id, "agent_session_reset", "system", {"reason": reason})
        self.directory(job_id)
        return session_id

    def sanitize(self, job_id: str) -> None:
        """Keep DATA and validated reports; discard transient browser payloads/reasoning from persisted history.

        Tool calls and results keep their ids for a coherent conversation, but their payloads are never needed on
        resume: the agent must inspect the live tab again. CLI writes are private (umask 077) even before this pass.
        """
        directory = self.root / job_id
        if not directory.is_dir() or directory.is_symlink():
            return
        for path in directory.glob("projects/*/*.jsonl"):
            if path.is_symlink() or path.parent.is_symlink() or (directory / "projects").is_symlink():
                continue
            tmp: Path | None = None
            try:
                stat = path.stat()
                if self._scrubbed.get(path) == (stat.st_mtime_ns, stat.st_size):
                    continue
                if stat.st_size > 32_000_000:
                    raise ValueError("session exceeds size limit")
                rows = []
                for line in path.read_text().splitlines():
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("invalid transcript record")
                    if row.get("type") not in ("user", "assistant", "system"):
                        continue  # progress, tool snapshots and file history are not conversation records
                    row = {
                        k: v
                        for k, v in row.items()
                        if k
                        in {
                            "uuid",
                            "parentUuid",
                            "sessionId",
                            "timestamp",
                            "type",
                            "isSidechain",
                            "cwd",
                            "version",
                            "userType",
                            "message",
                            "requestId",
                            "isMeta",
                            "subtype",
                            "compactMetadata",
                            "logicalParentUuid",
                            "isCompactSummary",
                            "isVisibleInTranscriptOnly",
                        }
                    }
                    if row["type"] == "system":
                        # Preserve compact-boundary links, but never retain arbitrary system/tool prose.
                        row.pop("message", None)
                        rows.append(json.dumps(row, separators=(",", ":")))
                        continue
                    message = row.get("message")
                    if not isinstance(message, dict):
                        raise ValueError("invalid transcript message")
                    content = message.get("content")
                    if isinstance(content, list):
                        clean = []
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            kind = block.get("type")
                            if kind == "tool_use":
                                clean.append({"type": kind, "id": block.get("id"), "name": block.get("name"), "input": {}})
                            elif kind == "tool_result":
                                clean.append(
                                    {
                                        "type": kind,
                                        "tool_use_id": block.get("tool_use_id"),
                                        "content": "Transient browser output omitted. Inspect the live tab.",
                                    }
                                )
                            elif kind == "text":
                                text = str(block.get("text", ""))
                                if row["type"] == "assistant":
                                    try:
                                        parse_result(text)
                                    except ResultInvalid:
                                        text = "Transient browser reasoning omitted."
                                elif "DATA JSON (" not in text:
                                    text = "Transient content omitted."
                                clean.append({"type": "text", "text": safe_data(text)})
                        message["content"] = clean or [{"type": "text", "text": "Transient content omitted."}]
                    elif isinstance(content, str):
                        message["content"] = (
                            safe_data(content)
                            if row["type"] == "user" and "DATA JSON (" in content
                            else "Transient content omitted."
                        )
                    rows.append(json.dumps(row, separators=(",", ":")))
                with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temporary:
                    tmp = Path(temporary.name)
                    temporary.write("\n".join(rows) + "\n")
                os.replace(tmp, path)
                path.chmod(0o600)
                stat = path.stat()
                self._scrubbed[path] = (stat.st_mtime_ns, stat.st_size)
            except (ValueError, OSError):
                # Unreadable history is never resumed. Delete it so a fresh full-DATA run can recover.
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    log.warning("session redaction failed job=%s", job_id)
                    raise  # fail closed: this history must not be resumed
            finally:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)

        # Dedicated per-job config: ancillary debug/cache/subagent files are not needed to resume and may hold raw IO.
        def discard(path: Path) -> None:
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                shutil.rmtree(path)

        for child in directory.iterdir():
            if child.name != "projects" or child.is_symlink():
                discard(child)
                continue
            for project in child.iterdir():
                if project.is_symlink() or not project.is_dir():
                    discard(project)
                    continue
                for artifact in project.iterdir():
                    if artifact.is_symlink() or artifact.suffix != ".jsonl":
                        discard(artifact)

    def remove(self, job_id: str) -> None:
        if not JOB_NAME.fullmatch(job_id) or self.root.is_symlink():
            return
        directory = self.root / job_id
        if directory.is_symlink():
            directory.unlink()
        elif directory.is_dir():
            shutil.rmtree(directory)

    def cleanup(self, job_id: str) -> None:
        """After commit only. Failed filesystem cleanup is retried by maintenance."""
        try:
            with self.svc.db.try_session_lock(job_id, 3) as acquired:
                if acquired:
                    self.remove(job_id)
                    self.repo.clear(job_id)
        except Exception as error:  # noqa: BLE001 - after-commit cleanup is retried by maintenance
            log.warning("session cleanup deferred job=%s reason=%s", job_id, type(error).__name__)

    def sweep(self) -> None:
        if not self.root.is_dir() or self.root.is_symlink():
            return
        active = self.repo.active_jobs()
        for path in self.root.iterdir():
            if not JOB_NAME.fullmatch(path.name):
                continue
            # The directory is touched on every run. Never modify transcripts of an in-flight process.
            with self.svc.db.try_session_lock(path.name, 3) as acquired:
                if not acquired:
                    continue
                if path.is_symlink() or path.name not in active or time.time() - path.stat().st_mtime > RETENTION:
                    if (
                        path.is_symlink()
                        or path.name not in self.repo.active_jobs()
                        or time.time() - path.stat().st_mtime > RETENTION
                    ):
                        self.cleanup(path.name)
                else:
                    self.sanitize(path.name)
