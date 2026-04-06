"""Configuration management using settings.json."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LLMConfig:
    """LLM provider configuration."""

    provider: str = "anthropic"  # anthropic, openai
    api_key: str = ""
    model: str = "claude-sonnet-4-20250514"
    base_url: str = ""


@dataclass
class ToolConfig:
    """Tool-specific configuration."""

    # Alert tool
    alert_api_base: str = ""
    alert_api_key: str = ""

    # Ticket tool
    ticket_api_base: str = ""
    ticket_api_key: str = ""

    # E-commerce tool
    ecom_api_base: str = ""
    ecom_api_key: str = ""

    # Bash tool
    allowed_commands: list[str] = field(default_factory=list)
    blocked_commands: list[str] = field(default_factory=lambda: ["rm -rf /", "dd if=", ":(){:|:&};"])


@dataclass
class AgentConfig:
    """Main agent configuration."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    tools: ToolConfig = field(default_factory=ToolConfig)
    system_prompt: str = "你是一个运维助手，帮助处理告警和工单。"
    max_turns: int = 50
    max_budget_usd: float | None = None


def load_config(config_path: str | Path | None = None) -> AgentConfig:
    """Load configuration from settings.json.

    Looks for config in:
    1. Explicit path if provided
    2. Current directory
    3. ~/.pyagent/settings.json
    4. Environment variables

    Args:
        config_path: Optional explicit path to settings.json

    Returns:
        AgentConfig with loaded settings
    """
    config = AgentConfig()

    # Try to find settings.json
    paths_to_try = []
    if config_path:
        paths_to_try.append(Path(config_path))
    paths_to_try.extend([
        Path.cwd() / "settings.json",
        Path.home() / ".pyagent" / "settings.json",
    ])

    for path in paths_to_try:
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            config = _parse_config(data)
            break

    # Override with environment variables
    config = _apply_env_overrides(config)

    return config


def _parse_config(data: dict[str, Any]) -> AgentConfig:
    """Parse JSON data into AgentConfig."""
    config = AgentConfig()

    if "llm" in data:
        llm_data = data["llm"]
        config.llm = LLMConfig(
            provider=llm_data.get("provider", "anthropic"),
            api_key=llm_data.get("api_key", ""),
            model=llm_data.get("model", "claude-sonnet-4-20250514"),
            base_url=llm_data.get("base_url", ""),
        )

    if "tools" in data:
        tools_data = data["tools"]
        config.tools = ToolConfig(
            alert_api_base=tools_data.get("alert_api_base", ""),
            alert_api_key=tools_data.get("alert_api_key", ""),
            ticket_api_base=tools_data.get("ticket_api_base", ""),
            ticket_api_key=tools_data.get("ticket_api_key", ""),
            ecom_api_base=tools_data.get("ecom_api_base", ""),
            ecom_api_key=tools_data.get("ecom_api_key", ""),
            allowed_commands=tools_data.get("allowed_commands", []),
            blocked_commands=tools_data.get("blocked_commands", ["rm -rf /", "dd if=", ":(){:|:&};"]),
        )

    if "system_prompt" in data:
        config.system_prompt = data["system_prompt"]

    if "max_turns" in data:
        config.max_turns = data["max_turns"]

    if "max_budget_usd" in data:
        config.max_budget_usd = data["max_budget_usd"]

    return config


def _apply_env_overrides(config: AgentConfig) -> AgentConfig:
    """Apply environment variable overrides."""
    if api_key := os.environ.get("ANTHROPIC_API_KEY"):
        config.llm.api_key = api_key
    if api_key := os.environ.get("OPENAI_API_KEY"):
        config.llm.api_key = api_key
    if model := os.environ.get("PYAGENT_MODEL"):
        config.llm.model = model

    return config


def get_default_config_path() -> Path:
    """Get the default config directory."""
    config_dir = Path.home() / ".pyagent"
    config_dir.mkdir(exist_ok=True)
    return config_dir / "settings.json"
