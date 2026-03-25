"""execute_cmd tool — run shell commands with timeout, background mode, and output truncation."""

from __future__ import annotations

import subprocess
import threading
import time

from shipyard.config import settings
from shipyard.tools.base import ToolResult

BANNED_PATTERNS = [
    "rm -rf /",
    "mkfs",
    ":(){:|:&};:",
]

# Track background processes for status checking and cleanup
_background_processes: dict[int, dict] = {}  # pid -> {"proc", "command"}


def execute_cmd(
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
    for banned in BANNED_PATTERNS:
        if banned in command:
            return ToolResult(output=f"Error: Command blocked for safety: contains '{banned}'", is_error=True)

    if background:
        return _run_background(command)

    return _run_foreground(command, timeout)


def _run_foreground(command: str, timeout: int) -> ToolResult:
    """Run a command and wait for completion."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        partial = ""
        if e.stdout:
            partial += e.stdout.decode("utf-8", errors="replace")
        if e.stderr:
            partial += f"\nSTDERR:\n{e.stderr.decode('utf-8', errors='replace')}"

        return ToolResult(
            output=f"Command timed out after {timeout}s: {command}\n\n"
            f"Partial output before timeout:\n{partial}\n\n"
            f"HINT: If this is a server or long-running process, use background=True to start it "
            f"in the background. Example: execute_cmd(command='uvicorn app:app', background=True)",
            is_error=True,
        )
    except Exception as e:
        return ToolResult(output=f"Error executing command: {e}", is_error=True)

    output_parts: list[str] = []
    if result.stdout:
        output_parts.append(result.stdout.decode("utf-8", errors="replace"))
    if result.stderr:
        output_parts.append(f"STDERR:\n{result.stderr.decode('utf-8', errors='replace')}")

    output = "\n".join(output_parts) if output_parts else "(no output)"
    text = f"Exit code: {result.returncode}\n{output}"

    if len(text) > settings.max_tool_output_chars:
        half = settings.max_tool_output_chars // 2
        text = text[:half] + "\n\n... (output truncated) ...\n\n" + text[-half:]

    return ToolResult(output=text, is_error=result.returncode != 0)


def _run_background(command: str) -> ToolResult:
    """Start a process in the background and return immediately."""
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except Exception as e:
        return ToolResult(output=f"Error starting command: {e}", is_error=True)

    _background_processes[proc.pid] = {"proc": proc, "command": command}

    # Wait a few seconds to catch immediate startup errors
    time.sleep(3)

    if proc.poll() is not None:
        # Process exited quickly — likely an error
        stdout, stderr = proc.communicate()
        del _background_processes[proc.pid]

        output = ""
        if stdout:
            output += stdout.decode("utf-8", errors="replace")
        if stderr:
            output += f"\nSTDERR:\n{stderr.decode('utf-8', errors='replace')}"

        return ToolResult(
            output=f"Process exited immediately (exit code {proc.returncode}). "
            f"This was NOT started in the background.\n{output}",
            is_error=proc.returncode != 0,
        )

    return ToolResult(
        output=f"Started background process (PID {proc.pid}): {command}\n"
        f"Use check_background(pid={proc.pid}) to check its status.\n"
        f"Use stop_background(pid={proc.pid}) to stop it."
    )


def check_background(pid: int) -> ToolResult:
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

    if proc.poll() is not None:
        stdout, stderr = proc.communicate()
        del _background_processes[pid]

        output = ""
        if stdout:
            output += stdout.decode("utf-8", errors="replace")
        if stderr:
            output += f"\nSTDERR:\n{stderr.decode('utf-8', errors='replace')}"

        return ToolResult(
            output=f"Process {pid} has exited (exit code {proc.returncode}).\n"
            f"Command: {entry['command']}\n{output}",
            is_error=proc.returncode != 0,
        )

    return ToolResult(
        output=f"Process {pid} is still running.\n"
        f"Command: {entry['command']}\n"
        f"Use stop_background(pid={pid}) to stop it."
    )


def stop_background(pid: int) -> ToolResult:
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
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception as e:
        return ToolResult(output=f"Error stopping process: {e}", is_error=True)

    del _background_processes[pid]
    return ToolResult(output=f"Stopped process {pid} ({entry['command']}).")
