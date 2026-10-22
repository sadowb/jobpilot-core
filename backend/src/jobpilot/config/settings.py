from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MAX_WORKER_CONCURRENCY = 32  # the dashboard's agent stepper and the worker's connection pool are both sized from this
MAX_OPEN_BROWSERS = 64
DEFAULT_WORKER_CONCURRENCY = 4  # parallel agents; one WORKER_CONCURRENCY drives the Celery pool size and the admission cap
DEFAULT_MAX_OPEN_BROWSERS = 8  # 4 running agents + 4 parked approval browsers
NODE_PATTERN = r"^[a-z0-9][a-z0-9-]{0,31}$"  # lowercase: it is embedded in a Celery queue name (node.<name>)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    jobpilot_home: Path = Field(default_factory=lambda: Path.home() / "jobsClaude")
    jobpilot_vault: Path | None = None

    pghost: str = "127.0.0.1"
    pgport: int = 5433
    pgdatabase: str = "jobpilot"
    pguser: str = "jobpilot"
    pgpassword: str = ""

    backend_host: str = "127.0.0.1"  # a container binds 0.0.0.0 and publishes the port on host loopback only
    backend_port: int = 4100
    agent_model_browser: str = "claude-haiku-5-5"
    agent_model_cv: str = "claude-haiku-5-5"
    agent_effort: Literal["low", "medium", "high", "max"] = "medium"
    worker_concurrency: int = Field(default=DEFAULT_WORKER_CONCURRENCY, ge=1, le=MAX_WORKER_CONCURRENCY)
    max_open_browsers: int = Field(default=DEFAULT_MAX_OPEN_BROWSERS, ge=1, le=MAX_OPEN_BROWSERS)

    # Celery + Redis execution (docs/adr/0012-celery-redis-execution.md). Redis is broker only; PostgreSQL stays authoritative.
    jobpilot_node: str = Field(default="mac", pattern=NODE_PATTERN)
    # This process's Celery pool size (docker/compose `-c`). Same env var as worker_concurrency, but never overwritten by the
    # persisted runtime settings: worker_concurrency is the admission throttle, node_slots the real physical capacity.
    node_slots: int = Field(
        default=DEFAULT_WORKER_CONCURRENCY, ge=1, le=MAX_WORKER_CONCURRENCY, validation_alias=AliasChoices("WORKER_CONCURRENCY")
    )
    jobpilot_pool: Literal["prefork", "threads"] = "prefork"
    redis_host: str = "127.0.0.1"
    redis_port: int = Field(default=6380, ge=1, le=65535)
    redis_password: str = ""

    domain_gap_seconds: float = Field(default=5.0, ge=0)

    browser_budget_usd: float = Field(default=0, ge=0, le=100)
    browser_run_timeout_seconds: int = Field(default=0, ge=0, le=540)
    agent_stall_seconds: int = Field(default=900, ge=0)
    cv_budget_usd: float = Field(default=0.25, gt=0, le=100)
    # BrowserOS neo (docs/adr/0019): the only browser. Loopback, or the in-container relay (see docker/entrypoint.sh).
    neo_mcp_url: str = "http://127.0.0.1:9010/mcp"

    @field_validator("browser_run_timeout_seconds")
    @classmethod
    def validate_browser_timeout(cls, value: int) -> int:
        if 0 < value < 30:
            raise ValueError("timeout must be 0 (unlimited) or at least 30 seconds")
        return value

    @property
    def home(self) -> Path:
        return self.jobpilot_home

    @property
    def vault(self) -> Path:
        return self.jobpilot_vault or (self.home / "data" / "vault")

    @property
    def data_dir(self) -> Path:
        return self.home / "data"

    @property
    def redis_url(self) -> str:
        auth = f":{quote(self.redis_password, safe='')}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/0"

    @property
    def dsn(self) -> str:
        return f"host={self.pghost} port={self.pgport} dbname={self.pgdatabase} user={self.pguser} password={self.pgpassword}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workerConcurrency: int = Field(default=DEFAULT_WORKER_CONCURRENCY, ge=1, le=MAX_WORKER_CONCURRENCY)
    maxOpenBrowsers: int = Field(default=DEFAULT_MAX_OPEN_BROWSERS, ge=1, le=MAX_OPEN_BROWSERS)
    cvModel: Literal["claude-haiku-5-5"] = "claude-haiku-5-5"  # Haiku only (ADR 0020); there is no other model to pick
    effort: Literal["low", "medium", "high", "max"] = "medium"
    browserBudgetUsd: float = Field(default=0, ge=0, le=100)
    browserRunTimeoutSeconds: int = Field(default=0, ge=0, le=540)
    agentStallSeconds: int = Field(default=900, ge=0)
    cvBudgetUsd: float = Field(default=0.25, gt=0, le=100)
    domainGapSeconds: float = Field(default=5.0, ge=0)
    submitMode: Literal["review", "auto"] = "review"  # ADR 0026: review = the owner submits; auto = the agent submits

    @field_validator("browserRunTimeoutSeconds")
    @classmethod
    def validate_browser_timeout(cls, value: int) -> int:
        if 0 < value < 30:
            raise ValueError("timeout must be 0 (unlimited) or at least 30 seconds")
        return value
