"""Monitored, tool-free CLI completion for CV comparison. No browser or vault access."""

from __future__ import annotations

import json
import math
import tempfile
from collections.abc import Callable
from pathlib import Path

from ..config import Settings
from ..llm import LlmResult
from .claude import ClaudeRunError, child_environment, run_failure, run_stream


def compare_text(cfg: Settings, job_id: str, prompt: str, system: str, check: Callable[[], None]) -> LlmResult:
    check()
    env = child_environment(cfg.jobpilot_node)
    root = cfg.data_dir / "run/cv-selection"
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    args = [
        "claude",
        "-p",
        "--model",
        cfg.agent_model_cv,
        "--effort",
        cfg.agent_effort,
        "--output-format",
        "stream-json",
        "--verbose",
        "--safe-mode",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--no-chrome",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--tools",
        "",
        "--append-system-prompt",
        system,
        "--max-budget-usd",
        str(cfg.cv_budget_usd),
    ]
    with tempfile.TemporaryDirectory(prefix="selection-", dir=root) as directory:
        try:
            p = run_stream(
                args, prompt, cwd=Path(directory), env=env, check=check, stall_seconds=cfg.agent_stall_seconds, timeout=180
            )
        except FileNotFoundError:
            raise ClaudeRunError("CV comparison CLI missing", environment=True, reason="binary_missing") from None
    try:
        envelope = json.loads(p.stdout)
    except ValueError:
        raise ClaudeRunError("CV comparison returned invalid JSON", reason="invalid_json") from None
    if not isinstance(envelope, dict):
        raise ClaudeRunError("CV comparison returned invalid JSON", reason="invalid_json")
    failure = run_failure(p.returncode, p.stderr, envelope)
    if failure:
        raise failure
    response = envelope.get("result")
    if not isinstance(response, str):
        raise ClaudeRunError("CV comparison result missing", reason="invalid_json")
    usage = envelope.get("usage", {})
    usage = usage if isinstance(usage, dict) else {}

    def count(name: str) -> int:
        value = usage.get(name, 0)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    cost = envelope.get("total_cost_usd", 0)
    return LlmResult(
        response,
        count("input_tokens") + count("cache_read_input_tokens") + count("cache_creation_input_tokens"),
        count("output_tokens"),
        float(cost) if isinstance(cost, (float, int)) and math.isfinite(cost) and cost >= 0 else 0.0,
    )
