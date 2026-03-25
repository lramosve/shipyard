"""execute_cmd tool — run shell commands with timeout and output truncation."""

from __future__ import annotations

import asyncio

from shipyard.config import settings
from shipyard.tools.base import ToolResult

BANNED_PATTERNS = [
    "rm -rf /",
    "mkfs",
    ":(){:|:&};:",
]


async def execute_cmd(
    command: str,
    timeout: int = 120,
) -> ToolResult:
    """Execute a shell command and return its output.

    Args:
        command: The shell command to run.
        timeout: Max seconds to wait (default 30).
    """
    # Safety check
    for banned in BANNED_PATTERNS:
        if banned in command:
            return ToolResult(output=f"Error: Command blocked for safety: contains '{banned}'", is_error=True)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return ToolResult(output=f"Error: Command timed out after {timeout}s: {command}", is_error=True)
    except Exception as e:
        return ToolResult(output=f"Error executing command: {e}", is_error=True)

    output_parts: list[str] = []
    if stdout:
        output_parts.append(stdout.decode("utf-8", errors="replace"))
    if stderr:
        output_parts.append(f"STDERR:\n{stderr.decode('utf-8', errors='replace')}")

    output = "\n".join(output_parts) if output_parts else "(no output)"
    exit_code = proc.returncode

    result = f"Exit code: {exit_code}\n{output}"

    # Truncate
    if len(result) > settings.max_tool_output_chars:
        half = settings.max_tool_output_chars // 2
        result = result[:half] + "\n\n... (output truncated) ...\n\n" + result[-half:]

    return ToolResult(output=result, is_error=exit_code != 0)
