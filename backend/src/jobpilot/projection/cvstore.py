from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ..domain.cv import CV_FILENAME, TEX_FILENAME, cv_object_hash

_SHA = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class CvObject:
    sha: str
    dir: Path


class CvStore:
    """Content-addressed, immutable CV objects. Jobs and tracks symlink to them; nothing is copied twice."""

    def __init__(self, vault: Path) -> None:
        self.vault = vault
        self.root = vault / "_cv-store"

    def object_dir(self, sha: str) -> Path:
        if not _SHA.match(sha):
            raise ValueError("bad cv sha")
        target = (self.root / sha).resolve()
        if not target.is_relative_to(self.root.resolve()):
            raise ValueError("cv object outside store")
        return target

    def has(self, sha: str) -> bool:
        return (self.object_dir(sha) / CV_FILENAME).exists()

    def put(self, tex_path: Path, pdf_path: Path) -> CvObject:
        tex, pdf = tex_path.read_bytes(), pdf_path.read_bytes()
        sha = cv_object_hash(tex, pdf)
        d = self.object_dir(sha)
        if not (d / CV_FILENAME).exists():
            d.mkdir(parents=True, exist_ok=True)
            for name, data in ((TEX_FILENAME, tex), (CV_FILENAME, pdf)):
                p = d / name
                p.write_bytes(data)
                p.chmod(0o444)
        return CvObject(sha, d)

    def link(self, target_dir: Path, sha: str) -> None:
        """Relative symlinks `<target>/main.tex` and `<target>/Abdelhadi Marjane.pdf` -> store object."""
        if not self.has(sha):
            raise FileNotFoundError("cv object missing")
        target = target_dir.resolve()
        if not target.is_relative_to(self.vault.resolve()) or target == self.vault.resolve():
            raise ValueError("link target outside vault")
        target.mkdir(parents=True, exist_ok=True)
        obj = self.object_dir(sha).resolve()
        for name in (TEX_FILENAME, CV_FILENAME):
            link = target / name
            to = os.path.relpath(obj / name, target)
            if link.is_symlink() and os.readlink(link) == to:
                continue
            if link.exists() or link.is_symlink():
                link.unlink()
            link.symlink_to(to)

    def linked_sha(self, d: Path) -> str | None:
        p = d / CV_FILENAME
        if not p.is_symlink():
            return None
        target = p.resolve()
        root = self.root.resolve()
        if not target.is_relative_to(root) or len(target.relative_to(root).parts) != 2:
            return None
        sha, name = target.relative_to(root).parts
        return sha if _SHA.fullmatch(sha) and name == CV_FILENAME and self.has(sha) else None

    def current_sha(self, d: Path) -> str | None:
        """sha of what a dir holds: symlink target, or hash of real files if that object exists in the store."""
        linked = self.linked_sha(d)
        if linked:
            return linked
        try:
            sha = cv_object_hash((d / TEX_FILENAME).read_bytes(), (d / CV_FILENAME).read_bytes())
        except OSError:
            return None
        return sha if self.has(sha) else None

    def install_real(self, job_dir: Path, tex_path: Path, pdf_path: Path) -> CvObject:
        """Unique tailored CV: real, editable files in the job folder (and registered in the store)."""
        obj = self.put(tex_path, pdf_path)
        job_dir.mkdir(parents=True, exist_ok=True)
        for name, src in ((TEX_FILENAME, tex_path), (CV_FILENAME, pdf_path)):
            dest = job_dir / name
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            shutil.copyfile(src, dest)
            dest.chmod(0o644)
        return obj


@dataclass(frozen=True)
class Compiled:
    pdf_path: Path
    pages: int
    width: float
    height: float


def pdf_info(pdf: Path) -> tuple[int, float, float]:
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
    pages = int(m.group(1)) if (m := re.search(r"^Pages:\s+(\d+)", out, re.M)) else 0
    size = re.search(r"^Page size:\s+([\d.]+) x ([\d.]+)", out, re.M)
    return pages, float(size.group(1)) if size else 0.0, float(size.group(2)) if size else 0.0


def pdf_text(pdf: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True, timeout=20).stdout


@contextmanager
def compile_tex(tex_path: Path) -> Iterator[Compiled]:
    """Compile untrusted TeX; keep output only while the caller consumes it."""
    with tempfile.TemporaryDirectory(prefix="jp-cv-") as temp:
        out = Path(temp)
        # The runtime image ships a pre-filled bundle cache and sets TECTONIC_ONLY_CACHED=1: no network at compile time.
        offline = ["--only-cached"] if os.environ.get("TECTONIC_ONLY_CACHED") == "1" else []
        subprocess.run(
            ["tectonic", "-X", "compile", "--untrusted", *offline, "--outdir", str(out), str(tex_path)],
            capture_output=True,
            check=True,
            timeout=180,
        )
        produced = out / (tex_path.stem + ".pdf")
        pdf = out / CV_FILENAME
        if produced != pdf:
            shutil.copyfile(produced, pdf)
        pages, w, h = pdf_info(pdf)
        yield Compiled(pdf, pages, w, h)
