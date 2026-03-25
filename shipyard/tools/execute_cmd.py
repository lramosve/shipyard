"""execute_cmd tool — run shell commands with timeout, background mode, and output truncation."""

from __future__ import annotations

import asyncio
import signal
import sys

from shipyard.config import settings
from shipyard.tools.base import ToolResult

BANNED_PATTERNS = [
    "rm -rf /",
    "mkfs",
    ":(){:|:&};:",
]

# Track background processes for status checking and cleanup
_background_processes: dict[int, dict] = {}  # pid -> {"proc", "command", "output"}


async def execute_cmd(
    command: str,
    timeout: int = 120,
    background: bool = False,
) -> ToolResult:
    """Execute a shell command and return its output.

    Args:
        command: The shell command to run.
        timeout: Max seconds to wait (default 120). Ignored if background=True.
        background: If True, start the process in the background and return immediately
                    with the PID. Use check_background to see its output later.
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
    except Exception as e:
        return ToolResult(output=f"Error starting command: {e}", is_error=True)

    if background:
        # Store the process and return immediately
        _background_processes[proc.pid] = {
            "proc": proc,
            "command": command,
            "stdout_parts": [],
            "stderr_parts": [],
        }

        # Wait a few seconds to capture initial output (startup errors, etc.)
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
            # Process exited quickly — likely an error
            stdout, stderr = await proc.communicate()
            exit_code = proc.returncode
            del _background_processes[proc.pid]

            output = ""
            if stdout:
                output += stdout.decode("utf-8", errors="replace")
            if stderr:
                output += f"\nSTDERR:\n{stderr.decode('utf-8', errors='replace')}"

            return ToolResult(
                output=f"Process exited immediately (exit code {exit_code}). "
                f"This was NOT started in the background.\n{output}",
                is_error=exit_code != 0,
            )
        except asyncio.TimeoutError:
            # Good — process is still running
            pass

        return ToolResult(
            output=f"Started background process (PID {proc.pid}): {command}\n"
            f"Use check_background(pid={proc.pid}) to check its status.\n"
            f"Use stop_background(pid={proc.pid}) to stop it."
        )

    # Normal (foreground) execution
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        # Capture whatever output we got before timeout
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        except Exception:
            stdout, stderr = b"", b""

        partial_output = ""
        if stdout:
            partial_output += stdout.decode("utf-8", errors="replace")
        if stderr:
            partial_output += f"\nSTDERR:\n{stderr.decode('utf-8', errors='replace')}"

        return ToolResult(
            output=f"Command timed out after {timeout}s: {command}\n\n"
            f"Partial output before timeout:\n{partial_output}\n\n"
            f"HINT: If this is a server or long-running process, use background=True to start it "
            f"in the background. Example: execute_cmd(command='uvicorn app:app', background=True)",
            is_error=True,
        )
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


async def check_background(pid: int) -> ToolResult:
    """Check the status and output of a background process.

    Args:
        pid: Process ID returned by execute_cmd with background=True.
    """
    entry = _background_processes.get(pid)
    if not entry:
        return ToolResult(
            output=f"No background process with PID {pid}. "
            f"Active PIDs: {list(_background_processes.keys()) or 'none'}",
            is_error=True,
        )

    proc = entry["proc"]

    if proc.returncode is not None:
        # Process has exited
        stdout, stderr = b"", b""
        try:
            stdout, stderr = await proc.communicate()
        except Exception:
            pass

        output = ""
        if stdout:
            output += stdout.decode("utf-8", errors="replace")
        if stderr:
            output += f"\nSTDERR:\n{stderr.decode('utf-8', errors='replace')}"

        del _background_processes[pid]
        return ToolResult(
            output=f"Process {pid} has exited (exit code {proc.returncode}).\n"
            f"Command: {entry['command']}\n{output}",
            is_error=proc.returncode != 0,
        )

    # Still running
    return ToolResult(
        output=f"Process {pid} is still running.\n"
        f"Command: {entry['command']}\n"
        f"Use stop_background(pid={pid}) to stop it."
    )


async def stop_background(pid: int) -> ToolResult:
    """Stop a background process.

    Args:
        pid: Process ID to stop.
    """
    entry = _background_processes.get(pid)
    if not entry:
        return ToolResult(
            output=f"No background process with PID {pid}.",
            is_error=True,
        )

    proc = entry["proc"]
    try:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
    except Exception as e:
        return ToolResult(output=f"Error stopping process: {e}", is_error=True)

    del _background_processes[pid]
    return ToolResult(output=f"Stopped process {pid} ({entry['command']}).")
