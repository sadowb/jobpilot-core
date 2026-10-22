from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from .config import Settings


@dataclass
class LlmResult:
    text: str
    tokens_in: int
    tokens_out: int
    usd: float


def claude_once(
    cfg: Settings, model: str, prompt: str, system: str | None = None, max_budget_usd: float | None = None, timeout: int = 180
) -> LlmResult:
    """One-shot, tool-free completion through the Claude CLI (the same login as the browser agent)."""
    args = [
        "claude",
        "-p",
        "--model",
        model,
        "--effort",
        cfg.agent_effort,
        "--output-format",
        "json",
        "--safe-mode",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--no-chrome",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--tools",
        "",
    ]
    if system:
        args += ["--append-system-prompt", system]
    if max_budget_usd:
        args += ["--max-budget-usd", str(max_budget_usd)]
    p = subprocess.run(args, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=cfg.data_dir)
    if p.returncode != 0:
        raise RuntimeError(f"claude exited {p.returncode}: stdout={p.stdout[:1000]} stderr={p.stderr[:1000]}")
    try:
        j = json.loads(p.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"non-JSON model output: stdout={p.stdout[:1000]} stderr={p.stderr[:1000]}") from e
    if not isinstance(j, dict):
        raise RuntimeError("invalid JSON model output")
    if j.get("is_error"):
        raise RuntimeError(str(j.get("result", "agent error"))[:200])
    u = j.get("usage", {})
    return LlmResult(
        str(j.get("result", "")),
        int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0)),
        int(u.get("output_tokens", 0)),
        float(j.get("total_cost_usd", 0.0)),
    )
