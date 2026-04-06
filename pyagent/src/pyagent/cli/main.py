"""CLI entry point for PyAgent.

Usage:
    # Interactive mode
    pyagent

    # Single query
    pyagent "process alert A123"

    # With API keys via settings.json
    # Create ~/.pyagent/settings.json

    # With environment variables
    ANTHROPIC_API_KEY=sk-ant-... pyagent
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax

from .. import __version__
from ..config import AgentConfig, load_config
from ..core.query_engine import QueryEngine, QueryEngineConfig
from ..llm.anthropic import AnthropicClient
from ..llm.base import LLMClient
from ..tools.registry import ToolRegistry
from ..tools.builtin import BashTool, HttpTool, SearchTool
from ..ops import AlertTool, TicketTool, EcomTool

app = typer.Typer(
    name="pyagent",
    help="AI Agent framework for autoOps",
    add_completion=False,
)

console = Console()


def create_llm_client(config: AgentConfig) -> LLMClient:
    """Create LLM client from config."""
    if not config.llm.api_key:
        console.print(
            "[yellow]Warning: No API key configured. Set ANTHROPIC_API_KEY or add to settings.json[/yellow]"
        )

    if config.llm.provider == "anthropic":
        return AnthropicClient(
            api_key=config.llm.api_key,
            model=config.llm.model,
            base_url=config.llm.base_url or None,
        )
    else:
        raise ValueError(f"Unsupported provider: {config.llm.provider}")


def create_agent(config: AgentConfig | None = None) -> QueryEngine:
    """Create the agent with all tools."""
    if config is None:
        config = load_config()

    # Initialize LLM client
    llm = create_llm_client(config)

    # Initialize tool registry
    registry = ToolRegistry()

    # Register built-in tools
    registry.register(BashTool())
    registry.register(HttpTool())
    registry.register(SearchTool())

    # Register ops tools
    alert_tool = AlertTool()
    ticket_tool = TicketTool()
    ecom_tool = EcomTool()
    registry.register(alert_tool)
    registry.register(ticket_tool)
    registry.register(ecom_tool)

    # Configure tool metadata (for API endpoints)
    for tool in [alert_tool, ticket_tool, ecom_tool]:
        if tool.name == "alert_handler":
            tool.metadata = {
                "alert_api_base": config.tools.alert_api_base,
                "alert_api_key": config.tools.alert_api_key,
            }
        elif tool.name == "ticket_handler":
            tool.metadata = {
                "ticket_api_base": config.tools.ticket_api_base,
                "ticket_api_key": config.tools.ticket_api_key,
            }
        elif tool.name == "ecom_ops":
            tool.metadata = {
                "ecom_api_base": config.tools.ecom_api_base,
                "ecom_api_key": config.tools.ecom_api_key,
            }

    # Create engine config
    engine_config = QueryEngineConfig(
        llm_client=llm,
        tool_registry=registry,
        system_prompt=config.system_prompt,
        max_turns=config.max_turns,
        max_budget_usd=config.max_budget_usd,
    )

    return QueryEngine(engine_config)


async def run_interactive(config: AgentConfig | None = None) -> None:
    """Run interactive mode."""
    console.print(Panel.fit(
        "[bold cyan]PyAgent[/bold cyan] - AI Agent for autoOps\n"
        "Type your queries or 'exit' to quit.",
        border_style="cyan",
    ))
    console.print()

    if config is None:
        config = load_config()

    agent = create_agent(config)

    while True:
        try:
            user_input = await asyncio.to_thread(
                console.input,
                "[bold green]You[/bold green]: ",
            )
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Exiting...[/yellow]")
            break

        if not user_input.strip():
            continue

        if user_input.lower() in ("exit", "quit", "q"):
            console.print("[yellow]Goodbye![/yellow]")
            break

        console.print()
        console.print("[bold cyan]Assistant[/bold cyan]: ", end="")

        try:
            async for event in agent.stream(user_input):
                if isinstance(event, str):
                    console.print(event, end="", style="cyan")
                elif hasattr(event, "data"):
                    # Tool result
                    result = event
                    if hasattr(result, "is_success") and not result.is_success():
                        console.print(
                            f"\n[bold red]Error[/bold red]: {result.error}",
                            style="red",
                        )
                    else:
                        data = result.data if hasattr(result, "data") else event
                        console.print()
                        console.print(f"[dim]Tool result: {data}[/dim]")

            console.print()
            console.print()

        except Exception as e:
            console.print(f"\n[bold red]Error[/bold red]: {e}")
            import traceback
            traceback.print_exc()
            console.print()


async def run_query(query: str, config: AgentConfig | None = None) -> None:
    """Run a single query."""
    if config is None:
        config = load_config()

    agent = create_agent(config)

    console.print(f"[bold cyan]Query[/bold cyan]: {query}")
    console.print()

    try:
        async for event in agent.stream(query):
            if isinstance(event, str):
                console.print(event, end="", style="cyan")
            elif hasattr(event, "data"):
                result = event
                console.print()
                console.print(f"[dim]Tool result: {result.data}[/dim]")

        console.print()

    except Exception as e:
        console.print(f"\n[bold red]Error[/bold red]: {e}")
        sys.exit(1)


@app.command()
def main(
    query: str | None = typer.Argument(
        default=None,
        help="Query to execute (if not provided, runs interactive mode)",
    ),
    config_path: str | None = typer.Option(
        None,
        "--config",
        help="Path to settings.json",
    ),
    version: bool = typer.Option(
        default=False,
        help="Show version",
    ),
) -> None:
    """PyAgent - AI Agent for autoOps."""
    if version:
        console.print(f"PyAgent v{__version__}")
        return

    # Load config
    config = None
    if config_path:
        config = load_config(config_path)
    else:
        try:
            config = load_config()
        except Exception:
            pass  # Use defaults

    if query:
        asyncio.run(run_query(query, config))
    else:
        asyncio.run(run_interactive(config))


if __name__ == "__main__":
    app()
