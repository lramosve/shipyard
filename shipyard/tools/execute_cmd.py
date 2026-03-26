"""execute_cmd tool — run shell commands with timeout, background mode, and output truncation."""

from __future__ import annotations

import os
import signal
import subprocess
import time

from shipyard.config import settings
from shipyard.tools.base import ToolResult

BANNED_PATTERNS = [
    "rm -rf /",
    "mkfs",
    ":(){:|:&};:",
]

# Commands that should always run in background mode
SERVER_PATTERNS = [
    "uvicorn", "gunicorn", "flask run", "npm start", "npm run dev",
    "npm run start", "next dev", "next start", "yarn dev", "yarn start",
    "python -m http.server", "python3 -m http.server", "ng serve",
    "rails server", "rails s", "cargo run", "go run",
]

# Track background processes for status checking and cleanup
_background_processes: dict[int, dict] = {}


def _is_server_command(command: str) -> bool:
    """Detect if a command is a server/long-running process."""
    cmd_lower = command.lower()
    return any(pattern in cmd_lower for pattern in SERVER_PATTERNS)


def execute_cmd(
    command: str,
    timeout: int = 120,
    background: bool = False,
) -> ToolResult:
    """Execute a shell command and return its output.

    Args:
        command: The shell command to run.
        timeout: Max seconds to wait (default 120). Ignored if background=True.
        background: If True, start the process in the background and return immediately.
    """
    for banned in BANNED_PATTERNS:
        if banned in command:
            return ToolResult(output=f"Error: Command blocked for safety: contains '{banned}'", is_error=True)

    # Auto-detect servers and force background mode
    if not background and _is_server_command(command):
        background = True

    if background:
        return _run_background(command)

    return _run_foreground(command, timeout)


def _run_foreground(command: str, timeout: int) -> ToolResult:
    """Run a command and wait for completion."""
    proc = None
    try:
        # Use Popen instead of run() so we can kill properly on timeout
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Kill the entire process tree on Windows
        _kill_process_tree(proc)
        # Collect whatever output we can (non-blocking)
        partial = _safe_collect_output(proc)
        return ToolResult(
            output=f"Command timed out after {timeout}s: {command}\n\n"
            f"Partial output:\n{partial}\n\n"
            f"HINT: If this is a server or long-running process, use background=True.",
            is_error=True,
        )
    except KeyboardInterrupt:
        if proc:
            _kill_process_tree(proc)
        return ToolResult(output=f"Command interrupted by user: {command}", is_error=True)
    except Exception as e:
        if proc:
            _kill_process_tree(proc)
        return ToolResult(output=f"Error executing command: {e}", is_error=True)

    output_parts: list[str] = []
    if stdout:
        output_parts.append(stdout.decode("utf-8", errors="replace"))
    if stderr:
        output_parts.append(f"STDERR:\n{stderr.decode('utf-8', errors='replace')}")

    output = "\n".join(output_parts) if output_parts else "(no output)"
    text = f"Exit code: {proc.returncode}\n{output}"

    if len(text) > settings.max_tool_output_chars:
        half = settings.max_tool_output_chars // 2
        text = text[:half] + "\n\n... (output truncated) ...\n\n" + text[-half:]

    return ToolResult(output=text, is_error=proc.returncode != 0)


def _run_background(command: str) -> ToolResult:
    """Start a process in the background, logging output to a temp file."""
    import tempfile

    # Redirect output to temp files so we can read logs later
    try:
        log_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", prefix="shipyard_bg_", delete=False
        )
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=log_file,
            stderr=subprocess.STDOUT,  # merge stderr into stdout log
        )
    except Exception as e:
        return ToolResult(output=f"Error starting command: {e}", is_error=True)

    _background_processes[proc.pid] = {
        "proc": proc,
        "command": command,
        "log_path": log_file.name,
    }

    # Wait a few seconds to catch immediate startup errors
    time.sleep(3)

    if proc.poll() is not None:
        log_file.close()
        output = _read_log(log_file.name)
        del _background_processes[proc.pid]

        return ToolResult(
            output=f"Process exited immediately (exit code {proc.returncode}). "
            f"This was NOT started in the background.\n{output}",
            is_error=proc.returncode != 0,
        )

    # Read initial output (startup messages, early errors)
    log_file.flush()
    initial_output = _read_log(log_file.name, tail=20)

    return ToolResult(
        output=f"Started background process (PID {proc.pid}): {command}\n"
        f"Log file: {log_file.name}\n"
        f"Use check_background(pid={proc.pid}) to see logs and status.\n"
        f"Use stop_background(pid={proc.pid}) to stop it.\n\n"
        f"Initial output:\n{initial_output}"
    )


def check_background(pid: int) -> ToolResult:
    """Check the status and recent logs of a background process."""
    entry = _background_processes.get(pid)
    if not entry:
        return ToolResult(
            output=f"No background process with PID {pid}. "
            f"Active PIDs: {list(_background_processes.keys()) or 'none'}",
            is_error=True,
        )

    proc = entry["proc"]
    log_path = entry.get("log_path", "")
    recent_logs = _read_log(log_path, tail=30) if log_path else "(no log file)"

    if proc.poll() is not None:
        del _background_processes[pid]
        return ToolResult(
            output=f"Process {pid} has EXITED (exit code {proc.returncode}).\n"
            f"Command: {entry['command']}\n\n"
            f"=== Last output ===\n{recent_logs}",
            is_error=proc.returncode != 0,
        )

    return ToolResult(
        output=f"Process {pid} is RUNNING.\n"
        f"Command: {entry['command']}\n\n"
        f"=== Recent logs ===\n{recent_logs}\n\n"
        f"Use stop_background(pid={pid}) to stop it."
    )


def stop_background(pid: int) -> ToolResult:
    """Stop a background process."""
    entry = _background_processes.get(pid)
    if not entry:
        return ToolResult(output=f"No background process with PID {pid}.", is_error=True)

    proc = entry["proc"]
    _kill_process_tree(proc)
    del _background_processes[pid]
    return ToolResult(output=f"Stopped process {pid} ({entry['command']}).")


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill a process and all its children. Works on Windows and Unix."""
    if proc is None:
        return
    try:
        if os.name == "nt":
            # Windows: taskkill /T kills the entire process tree
            subprocess.run(
                f"taskkill /F /T /PID {proc.pid}",
                shell=True,
                capture_output=True,
                timeout=10,
            )
        else:
            # Unix: kill the process group
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        # Last resort
        try:
            proc.kill()
        except Exception:
            pass


def _read_log(log_path: str, tail: int = 30) -> str:
    """Read the last N lines from a log file."""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        if not lines:
            return "(log file is empty)"
        return "".join(lines[-tail:])
    except Exception:
        return "(could not read log file)"


def _safe_collect_output(proc: subprocess.Popen) -> str:
    """Try to collect output from a killed process without blocking."""
    output = ""
    try:
        stdout, stderr = proc.communicate(timeout=3)
        if stdout:
            output += stdout.decode("utf-8", errors="replace")
        if stderr:
            output += f"\nSTDERR:\n{stderr.decode('utf-8', errors='replace')}"
    except Exception:
        output = "(could not collect output)"
    return output
