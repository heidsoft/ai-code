"""Bash tool for executing shell commands.

Based on Claude Code's BashTool implementation:
- Execute shell commands
- Support timeout
- Track execution
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass

from ..base import Tool, ToolResult, ToolUseContext


@dataclass
class BashTool(Tool[dict, str]):
    """Execute bash commands.

    Executes shell commands and returns the output.
    Non-concurrent by default (shell commands can have side effects).

    Input schema:
        {
            "command": str,      # The command to execute
            "timeout": int = 30, # Timeout in seconds
            "cwd": str = None,   # Working directory
        }
    """

    name = "bash"
    description = "Execute a bash command. Use for shell operations, file manipulation, and running scripts."

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 30)",
                    "default": 30,
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory for the command",
                },
            },
            "required": ["command"],
        }

    def is_read_only(self, args: dict) -> bool:
        """Bash is rarely read-only."""
        cmd = args.get("command", "").lower()
        # Check for read-only indicators
        read_only_prefixes = ("cat ", "head ", "tail ", "grep ", "ls ", "echo ")
        return any(cmd.startswith(prefix) for prefix in read_only_prefixes)

    async def call(
        self,
        args: dict,
        context: ToolUseContext,
    ) -> ToolResult[str]:
        """Execute the bash command."""
        command = args.get("command", "")
        timeout = args.get("timeout", 30)
        cwd = args.get("cwd")

        if not command:
            return ToolResult(data=None, error="No command provided")

        # Check for dangerous commands
        dangerous = ["rm -rf", "dd if=", ":(){:|:&};:", "> /dev/sda"]
        for d in dangerous:
            if d in command:
                return ToolResult(
                    data=None,
                    error=f"Command blocked for safety: {d}",
                )

        try:
            # Check if bash is available
            bash_path = shutil.which("bash")
            shell = bash_path or "/bin/sh"

            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                return ToolResult(
                    data=None,
                    error=f"Command timed out after {timeout}s",
                )

            output = ""
            if stdout:
                output += stdout.decode()
            if stderr:
                output += "\n[stderr]\n" + stderr.decode()

            if process.returncode != 0 and not output:
                return ToolResult(
                    data=output,
                    error=f"Command failed with code {process.returncode}",
                )

            return ToolResult(data=output or "[no output]")

        except Exception as e:
            return ToolResult(data=None, error=str(e))
