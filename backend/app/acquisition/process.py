from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error

ProcessLineCallback = Callable[[str], Awaitable[None]]
ProcessAbortCallback = Callable[[], bool]
logger = logging.getLogger("siksik.acquisition.process")


@dataclass(frozen=True, slots=True)
class ProcessResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    output_truncated: bool = False


async def _read_bounded(
    stream: asyncio.StreamReader | None,
    limit: int,
    on_line: ProcessLineCallback | None = None,
) -> tuple[bytes, bool]:
    if stream is None:
        return b"", False
    captured = bytearray()
    pending_line = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(64 * 1024)
        if not chunk:
            break
        remaining = limit - len(captured)
        if remaining > 0:
            captured.extend(chunk[:remaining])
        if len(chunk) > max(remaining, 0):
            truncated = True
        if on_line is not None:
            pending_line.extend(chunk)
            while True:
                newline = pending_line.find(b"\n")
                if newline < 0:
                    break
                raw_line = bytes(pending_line[:newline])
                del pending_line[: newline + 1]
                await _emit_process_line(
                    on_line,
                    raw_line.decode("utf-8", errors="replace").rstrip("\r"),
                )
            if len(pending_line) > MAX_STREAM_LINE_BYTES:
                raw_line = bytes(pending_line[:MAX_STREAM_LINE_BYTES])
                pending_line.clear()
                await _emit_process_line(
                    on_line,
                    raw_line.decode("utf-8", errors="replace"),
                )
    if on_line is not None and pending_line:
        await _emit_process_line(
            on_line,
            bytes(pending_line).decode("utf-8", errors="replace").rstrip("\r"),
        )
    return bytes(captured), truncated


async def _emit_process_line(callback: ProcessLineCallback, line: str) -> None:
    try:
        await callback(line)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Progress is best-effort; never stop draining stdout and deadlock the child process.
        logger.warning(
            "process_stdout_callback_failed",
            extra={"callback_error_type": type(exc).__name__},
        )


async def _stop_child(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.kill()
    except (ProcessLookupError, OSError):
        pass
    try:
        await process.wait()
    except (ProcessLookupError, OSError):
        pass


async def _wait_child(
    process: asyncio.subprocess.Process,
    *,
    timeout: float,
    abort_when: ProcessAbortCallback | None,
    abort_poll_s: float,
    abort_grace_s: float,
    operation: str,
    timeout_category: ErrorCategory,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    abort_ready_at: float | None = None
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            await _stop_child(process)
            raise acquisition_error(
                timeout_category,
                f"{operation} melewati batas waktu.",
                retryable=True,
            )
        slice_s = remaining if abort_when is None else min(max(abort_poll_s, 0.05), remaining)
        try:
            await asyncio.wait_for(process.wait(), timeout=slice_s)
            return
        except asyncio.TimeoutError:
            if abort_when is None:
                continue
            if abort_when():
                now = loop.time()
                if abort_ready_at is None:
                    abort_ready_at = now
                if now - abort_ready_at >= abort_grace_s:
                    logger.info(
                        "process_aborted_after_idle",
                        extra={"operation": operation},
                    )
                    await _stop_child(process)
                    return
            else:
                abort_ready_at = None


async def run_process(
    argv: Sequence[str],
    *,
    timeout: float,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = False,
    output_limit_bytes: int = 1024 * 1024,
    not_found_category: ErrorCategory = ErrorCategory.DEPENDENCY_NOT_FOUND,
    timeout_category: ErrorCategory = ErrorCategory.ADB_TIMEOUT,
    failure_category: ErrorCategory = ErrorCategory.ADB_COMMAND_FAILED,
    operation: str = "dependency_command",
    on_stdout_line: ProcessLineCallback | None = None,
    abort_when: ProcessAbortCallback | None = None,
    abort_poll_s: float = 1.0,
    abort_grace_s: float = 2.0,
) -> ProcessResult:
    command = tuple(str(value) for value in argv)
    if not command or timeout <= 0 or output_limit_bytes < 0:
        raise ValueError("command, timeout, and output limit are invalid")
    if any(not value or "\x00" in value for value in command):
        raise acquisition_error(
            ErrorCategory.VALIDATION_ERROR,
            "Argumen proses tidak valid.",
        )
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise acquisition_error(
            not_found_category,
            f"Dependency untuk {operation} tidak dapat dijalankan.",
        ) from exc

    stdout_task = asyncio.create_task(
        _read_bounded(process.stdout, output_limit_bytes, on_stdout_line)
    )
    stderr_task = asyncio.create_task(_read_bounded(process.stderr, output_limit_bytes))
    try:
        await _wait_child(
            process,
            timeout=timeout,
            abort_when=abort_when,
            abort_poll_s=abort_poll_s,
            abort_grace_s=abort_grace_s,
            operation=operation,
            timeout_category=timeout_category,
        )
    except asyncio.CancelledError:
        await _stop_child(process)
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise
    except AcquisitionError:
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise

    try:
        (stdout, stdout_truncated), (stderr, stderr_truncated) = await asyncio.wait_for(
            asyncio.gather(stdout_task, stderr_task),
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        stdout_task.cancel()
        stderr_task.cancel()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        stdout, stdout_truncated = b"", True
        stderr, stderr_truncated = b"", True
    result = ProcessResult(
        argv=command,
        returncode=int(process.returncode or 0),
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        output_truncated=stdout_truncated or stderr_truncated,
    )
    if check and result.returncode != 0:
        raise acquisition_error(
            failure_category,
            f"{operation} gagal dengan kode keluar {result.returncode}.",
            dependency_exit_code=result.returncode,
        )
    return result


MAX_STREAM_LINE_BYTES = 256 * 1024


def sanitized_environment() -> dict[str, str]:
    allowed = {
        "ANDROID_HOME",
        "ANDROID_SDK_ROOT",
        "GRADLE_USER_HOME",
        "HOME",
        "JAVA_HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "TMPDIR",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}
