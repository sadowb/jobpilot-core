"""Start the local API server. Celery workers execute jobs in separate processes."""

from __future__ import annotations

import logging

from .config import get_settings


def run() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    # Open SSE streams (the dashboard keeps one) must not block restarts: drop connections after 3 s.
    uvicorn.run(
        "jobpilot.main:app",
        host=get_settings().backend_host,
        port=get_settings().backend_port,
        log_level="info",
        server_header=False,
        access_log=False,
        timeout_graceful_shutdown=3,
    )


if __name__ == "__main__":
    run()
