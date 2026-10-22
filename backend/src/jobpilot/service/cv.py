"""Cv operations behind the shared Services facade."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..domain.cv import (
    CV_FILENAME,
    PLACEHOLDER,
    TEX_FILENAME,
    citation_issues,
    cv_object_hash,
    lint_overleaf,
    tailor_regions,
)
from ..domain.state_machine import PRE_SUBMIT_ORDER
from ..projection.cvstore import pdf_info
from ..projection.vault import commit_cv, ensure_vault, job_dir_for
from ..repository.cv import CvRepository
from ..repository.database import J
from ._support import log

if TYPE_CHECKING:
    from .application import Services


class CvService:
    def __init__(self, application: Services) -> None:
        self.application = application
        self.repository = CvRepository(application.db)

    def cv_check(self, job_id: str) -> dict[str, Any]:
        j = self.application.job(job_id)
        d = job_dir_for(self.application.cfg.vault, j["company_name"] or "Unknown", j["role"], job_id)
        failed: list[str] = []
        warnings = 0
        try:
            tex_bytes, pdf = (d / TEX_FILENAME).read_bytes(), d / CV_FILENAME
            tex = tex_bytes.decode()
            version = self.repository.find_cv_versions_for_cv_check(j["cv_version_id"])
            if not version or cv_object_hash(tex_bytes, pdf.read_bytes()) != version["sha256"]:
                failed.append("object")
            if PLACEHOLDER.search(tex):
                failed.append("placeholder")  # e.g. the US phone number was never filled in
            uploaded = tex.startswith("% JP-UPLOADED-PDF")  # owner's own PDF: used as-is, no LaTeX to lint
            if not uploaded:
                issues = lint_overleaf(tex)
                warnings = sum(i.level == "warn" for i in issues)
                if any(i.level == "error" for i in issues):
                    failed.append("overleaf")
            pages = pdf_info(pdf)[0]
            if pages != 1 and not (uploaded and pages == 2):
                failed.append("one_page")
            if j["cv_mode"] == "tailor":
                # base CVs are the user's own approved, hash-pinned objects; only generated text needs fact citations
                facts = {r["id"] for r in self.repository.list_facts_for_cv_check()}
                regions = tailor_regions(tex)
                if not regions or any(citation_issues(region, facts) for region in regions.values()):
                    failed.append("citations")
        except (OSError, UnicodeError, subprocess.SubprocessError):
            failed.append("files")
        result = {"ok": not failed, "failed": failed, "warnings": warnings}
        self.application.event(job_id, "cv_check", "system", result)
        return result

    def cv_use_base(self, job_id: str) -> dict[str, str]:
        self.repository.lock_job(job_id)
        j = self.application.job(job_id)
        if self.application.submit_clicked(job_id) or j["status"] not in PRE_SUBMIT_ORDER or j["cv_mode"] != "base":
            raise PermissionError("CV cannot change after submission begins")
        if not j["cv_track_id"]:
            raise ValueError("no cv track chosen")
        cur = self.repository.find_cv_tracks_for_cv_use_base(j["cv_track_id"])
        if not cur:
            raise ValueError(f"track {j['cv_track_id']} has no version")
        if j["cv_version_id"] == cur["vid"]:
            return {"sha": cur["sha256"]}
        d = job_dir_for(self.application.cfg.vault, j["company_name"] or "Unknown", j["role"], job_id)
        self.application.store.link(d, cur["sha256"])
        self.repository.update_jobs_for_cv_use_base(cur["vid"], job_id)
        resume = j["status"] in ("ready", "awaiting_approval")
        self.application.invalidate_form(job_id)
        if resume:
            self.application.resume(job_id)
        self.application.queue_cv_job(job_id)
        self.application.event(job_id, "cv", "system", {"mode": "base", "track": j["cv_track_id"], "sha": cur["sha256"]})
        self.application.project(job_id)
        return {"sha": cur["sha256"]}

    def read_track_tex(self, track: str) -> str:
        v = self.repository.find_cv_tracks_for_read_track_tex(track)
        if not v:
            raise LookupError("no such track version")
        return (self.application.store.object_dir(v["sha256"]) / "main.tex").read_text()

    def queue_cv_job(self, job_id: str, *, include_pdf: bool = False) -> None:
        self.repository.insert_cv_commit_jobs_for_queue_cv_job(job_id, include_pdf)

    def queue_cv_version(self, version: int, *, include_pdf: bool = False) -> None:
        self.repository.insert_cv_commit_versions_for_queue_cv_version(version, include_pdf)

    def register_track(
        self, track: str, title: str, keywords: list[str], mode: str, obj_sha: str, tex_sha: str, market: str = "any"
    ) -> int:
        self.repository.insert_cv_tracks_for_register_track(track, title, J(keywords), mode, market)
        row = self.repository.insert_cv_versions_for_register_track(track, obj_sha, tex_sha, f"track {track}")
        assert row
        version = int(row["id"])
        self.repository.update_cv_tracks_for_register_track(version, track)
        self.application.store.link(self.application.cfg.vault / "CV Tracks" / track, obj_sha)
        self.application.queue_cv_version(version, include_pdf=True)
        self.application.event(None, "track_updated", "user", {"track": track, "version": version})
        return version

    def register_tailored(self, job_id: str, obj_sha: str, tex_sha: str, cache_key: str) -> int:
        self.repository.lock_job(job_id)
        if self.application.submit_clicked(job_id):
            raise PermissionError("CV cannot change after submission begins")
        j = self.application.job(job_id)
        row = self.repository.insert_cv_versions_for_register_tailored(
            j["cv_track_id"], obj_sha, tex_sha, f"tailor-cache:{cache_key}", job_id
        )
        assert row
        self.application.event(job_id, "cv_object_registered", "system", {"version": row["id"], "sha": obj_sha})
        return int(row["id"])

    def use_tailored(self, job_id: str, obj_sha: str, version_id: int) -> None:
        self.repository.lock_job(job_id)
        if self.application.submit_clicked(job_id):
            raise PermissionError("CV cannot change after submission begins")
        j = self.application.job(job_id)
        d = job_dir_for(self.application.cfg.vault, j["company_name"] or "Unknown", j["role"], job_id)
        self.application.store.link(d, obj_sha)
        self.repository.update_jobs_for_use_tailored(version_id, job_id)
        self.application.invalidate_form(job_id)
        self.application.queue_cv_version(version_id)
        self.application.queue_cv_job(job_id)
        self.application.event(job_id, "cv", "agent", {"mode": "tailor", "sha": obj_sha})
        self.application.project(job_id)

    def sweep_cv_commits(self, limit: int = 100) -> None:
        # Re-read queue rows under the lock: an API and worker cannot commit a stale snapshot.
        with self.application.db.session_lock("cv-commit-sweep", 3):
            self.application._sweep_cv_commits(limit)

    def _lock_recovered(self, moved_to: Path) -> None:
        """A stale vault git lock was moved aside (never deleted): leave a trace the owner and the logs can find."""
        with self.application.db.transaction():
            self.application.event(None, "vault_lock_recovered", "system", {"movedTo": moved_to.name})

    def _sweep_cv_commits(self, limit: int) -> None:
        versions = self.repository.list_cv_commit_versions_for_sweep_cv_commits(limit)
        jobs = self.repository.list_cv_commit_jobs_for_sweep_cv_commits(limit)
        for item in [*versions, *jobs]:
            try:
                ensure_vault(self.application.cfg.vault)
                if "version_id" in item:
                    version = item["version_id"]
                    paths = [f"_cv-store/{item['sha256']}/{TEX_FILENAME}"]
                    if item["include_pdf"]:
                        paths.append(f"_cv-store/{item['sha256']}/{CV_FILENAME}")
                        # All tracks that currently reference this object (hash may be shared).
                        for track in self.repository.list_cv_tracks_for_sweep_cv_commits(version):
                            paths += [f"CV Tracks/{track['id']}/{TEX_FILENAME}", f"CV Tracks/{track['id']}/{CV_FILENAME}"]
                    commit = commit_cv(
                        self.application.cfg.vault,
                        f"cv: object {item['sha256'][:8]}",
                        paths,
                        db=self.application.db,
                        on_recovered=self._lock_recovered,
                    )
                    with self.application.db.transaction():
                        self.repository.update_cv_versions_for_sweep_cv_commits(commit, version)
                        self.repository.delete_cv_commit_versions_for_sweep_cv_commits(version, item["generation"])
                else:
                    j = self.application.job(item["job_id"])
                    d = job_dir_for(self.application.cfg.vault, j["company_name"] or "Unknown", j["role"], j["id"])
                    paths = [str((d / TEX_FILENAME).relative_to(self.application.cfg.vault))]
                    if item["include_pdf"]:
                        sha = self.application.store.current_sha(d)
                        if not sha:
                            raise ValueError("submitted CV is not a store object")
                        paths += [
                            str((d / CV_FILENAME).relative_to(self.application.cfg.vault)),
                            f"_cv-store/{sha}/{TEX_FILENAME}",
                            f"_cv-store/{sha}/{CV_FILENAME}",
                        ]
                    commit_cv(
                        self.application.cfg.vault,
                        f"cv: job {j['id']}",
                        paths,
                        db=self.application.db,
                        on_recovered=self._lock_recovered,
                    )
                    self.repository.delete_cv_commit_jobs_for_sweep_cv_commits(j["id"], item["generation"])
            except Exception:  # noqa: BLE001 - durable queue keeps failed commits retryable
                log.exception("CV commit failed; queued for retry")
