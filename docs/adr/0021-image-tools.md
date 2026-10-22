# 0021. Runtime image tools: poppler-utils, pinned Tectonic with an offline bundle cache, pinned claude-code

Status: accepted (2026-10-17)

## Context
The compose image shipped neither `pdfinfo`/`pdftotext` nor a LaTeX engine. `pdf_text` raised `FileNotFoundError`
(an `OSError`), which `CvSelectionService._candidates` swallowed, so **every CV was skipped** and CV selection had
nothing to choose from; track `.tex` compilation and uploads could not work either. `claude-code` was installed
unpinned and the dev tools (mypy, ruff, celery-types) were in the one lock file the image installed.

## Decision
1. **PDF text/info: `poppler-utils`** from Debian bookworm (apt, arm64 + amd64, `-layout` text the selector was
   written against, no code change). Considered `pypdfium2`/`pypdf`: removes an apt package but changes text layout
   and the `pdfinfo` page-size parsing for no real saving.
2. **LaTeX: Tectonic 0.17.0**, the upstream static musl release binary (9-10 MB), per-architecture SHA-256 checked in
   the Dockerfile. It is not packaged in bookworm, and TeX Live (`texlive-latex-extra`, fonts) is several hundred MB.
   Tectonic downloads its bundle lazily, per file, so loading a package is not enough: `docker/tex-warmup.sh` compiles
   `docker/tex-warmup.tex` (every package of the `domain/cv.py` allowlist) and each font package as roman, sans and mono
   default at 10/11/12 pt with every size command, shape and integer `\fontsize` 5-26 into `TECTONIC_CACHE_DIR=/opt/tectonic-cache`, then re-compiles with `--only-cached` to
   prove the cache is complete. The runtime sets `TECTONIC_ONLY_CACHED=1`; `compile_tex` adds `--only-cached`, so
   **no network at compile time**. A package outside the allowlist fails with a compile error instead of fetching.
   `tests/test_docker_assets.py` fails when the allowlist and the warm-up document diverge.
3. **`@anthropic-ai/claude-code` pinned** (`CLAUDE_CODE_VERSION`, currently 2.1.302); bumping it is a deliberate edit.
4. **Dev tools split out:** `backend/requirements.lock` is runtime only (hashed, installed by the image);
   `backend/requirements-dev.lock` (constrained by it) adds mypy, ruff and celery-types and is what CI and
   developers sync. Removed the dangling `jp` console script and the `websockets` direct dependency.

## Verified (in the rebuilt container)
- `pdftotext`/`pdfinfo` on a real stored CV: 4.5 kB of text, 1 page. All 9 CV candidates now load (before: all skipped).
- All 8 LaTeX track sources compile offline with `--only-cached` in 0.4-0.8 s each. A first cache that only loaded the
  packages failed 6 of 8 (missing size-specific TFMs), which is why the warm-up sweeps sizes.
- Image 876 MB; runtime venv has no mypy/ruff; `claude` 2.1.302.

## Consequences
- Image build needs network once (apt, npm, GitHub release, Tectonic bundle). Rebuild after bumping any version.
- The bundle cache is whatever the bundle served at build time; reproducibility of CV bytes across rebuilds depends on
  Tectonic's bundle being immutable per version (it is versioned by URL), and CV objects stay content-addressed anyway.
