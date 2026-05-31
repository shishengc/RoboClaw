"""
Agent runtime configuration.
"""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass
class AgentConfig:
    model: str = "gpt-4o"
    max_steps: int = 12
    max_tool_retries: int = 1
    llm_timeout_seconds: float = 60.0
    tool_timeout_seconds: float = 180.0
    finalize_min_completion: float = 0.9
    auto_reset_on_task_complete: bool = True
    trajectory_dir: str = "trajectories"


def load_agent_config(config_path: str | Path | None = None) -> AgentConfig:
    path = Path(config_path) if config_path else _default_config_path()
    if not path.exists():
        return AgentConfig()

    data = json.loads(path.read_text(encoding="utf-8"))
    known: dict[str, Any] = {
        field: data[field]
        for field in AgentConfig.__dataclass_fields__
        if field in data
    }
    return AgentConfig(**known)


def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "agent_config.json"
