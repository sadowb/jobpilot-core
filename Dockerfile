# JobPilot runtime image: dependencies only. The repository is bind-mounted at the same absolute path as on the host
# (see compose.yaml), so CV paths, the vault and agent/ files resolve identically for the containers and for the
# browser on the host, which reads the CV paths the agent hands it.
# syntax=docker/dockerfile:1.7
FROM node:22-bookworm-slim AS node

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# poppler-utils: pdfinfo/pdftotext (CV page count and text, ADR 0021). socat: loopback relay to the host's Neo.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git tini socat poppler-utils \
 && rm -rf /var/lib/apt/lists/*
# Node is only needed for the agent CLI (`claude`) the worker starts. The version is pinned: bump it deliberately.
ARG CLAUDE_CODE_VERSION=2.1.302
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && npm install -g --omit=dev "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && npm cache clean --force
# Tectonic (LaTeX): static musl release binary, checksum-verified per architecture (not packaged in bookworm). Its
# bundle is fetched once, at build time, by running docker/tex-warmup.sh (docker/tex-warmup.tex plus every allowlisted font family) into a world-readable cache; the runtime
# compiles with --only-cached (backend/src/jobpilot/projection/cvstore.py), so it never touches the network.
ARG TARGETARCH
ARG TECTONIC_VERSION=0.17.0
ARG TECTONIC_SHA256_ARM64=b10954a95404f3ab2328d2fa59a5ebab8e657f893fab096f98be8db7c0c979b8
ARG TECTONIC_SHA256_AMD64=8533d07f9ccbd7a65824b9e0459041bca34af1eb33daba48f59215593753a3b7
ENV TECTONIC_CACHE_DIR=/opt/tectonic-cache TECTONIC_ONLY_CACHED=1
COPY docker/tex-warmup.tex /tmp/tex-warmup/main.tex
COPY docker/tex-warmup.sh /usr/local/bin/tex-warmup
RUN set -eu; \
    case "${TARGETARCH}" in \
      arm64) arch=aarch64; sum="${TECTONIC_SHA256_ARM64}" ;; \
      amd64) arch=x86_64; sum="${TECTONIC_SHA256_AMD64}" ;; \
      *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    url="https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/tectonic-${TECTONIC_VERSION}-${arch}-unknown-linux-musl.tar.gz"; \
    python -c "import sys,urllib.request; urllib.request.urlretrieve(sys.argv[1], '/tmp/tectonic.tar.gz')" "$url"; \
    echo "${sum}  /tmp/tectonic.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/tectonic.tar.gz -C /usr/local/bin tectonic; \
    rm /tmp/tectonic.tar.gz; \
    mkdir -p "${TECTONIC_CACHE_DIR}"; \
    TECTONIC_ONLY_CACHED=0 sh /usr/local/bin/tex-warmup; \
    chmod -R a+rX "${TECTONIC_CACHE_DIR}"; \
    rm -rf /tmp/tex-warmup /usr/local/bin/tex-warmup
# Runtime Python dependencies only; mypy, ruff and celery-types live in backend/requirements-dev.lock.
COPY backend/requirements.lock /tmp/requirements.lock
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --require-hashes -r /tmp/requirements.lock \
 && rm /tmp/requirements.lock
ENV PATH=/opt/venv/bin:$PATH
# Non-root; compose sets the real uid/gid so files written into the bind mount belong to the host user.
RUN useradd --create-home --uid 1000 jobpilot
COPY docker/entrypoint.sh /usr/local/bin/jp-entrypoint
USER jobpilot
ENTRYPOINT ["tini", "--", "jp-entrypoint"]
