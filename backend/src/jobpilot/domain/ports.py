from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol


class AgentDriver(Protocol):
    name: str

    def preflight(self, cv: Path) -> None:
        """Raise before any browser tab is opened if this driver cannot run (unsupported model, missing CV file)."""
        ...

    def run_direct(self, job_id: str, instruction: str, *, worker: str, cv: Path, check: Callable[[], None]) -> str: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
