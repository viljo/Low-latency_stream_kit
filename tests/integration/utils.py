from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
import time
from collections import deque
from typing import Dict, List, Optional


class ProcessError(RuntimeError):
    """Raised when a subprocess exits with a non-zero status."""


def run(cmd: List[str], env: Optional[Dict[str, str]] = None, timeout: float = 60) -> List[str]:
    """Execute *cmd* streaming stdout/stderr and return collected lines."""

    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=merged_env,
        bufsize=1,
    )

    assert proc.stdout is not None
    output: List[str] = []
    start = time.monotonic()

    try:
        for line in iter(proc.stdout.readline, ""):
            output.append(line)
            sys.stdout.write(line)
            sys.stdout.flush()
            if proc.poll() is not None:
                break
            if time.monotonic() - start > timeout:
                proc.kill()
                raise TimeoutError(f"Command timed out after {timeout}s: {' '.join(cmd)}")
        ret = proc.wait(timeout=max(0.0, timeout - (time.monotonic() - start)))
    except Exception:
        proc.kill()
        proc.wait(timeout=5)
        raise

    if ret != 0:
        raise ProcessError(f"Command failed with exit code {ret}: {' '.join(cmd)}")

    return output


def wait_for(pattern: str, stream: io.TextIOBase, timeout_s: float) -> str:
    """Return the first line containing *pattern* within *timeout_s*."""

    deadline = time.monotonic() + timeout_s
    buffer = deque(maxlen=200)
    while True:
        if time.monotonic() >= deadline:
            snippet = "".join(buffer)
            raise TimeoutError(
                f"Timed out waiting for pattern {pattern!r}. Last output:\n{snippet}"
            )
        line = stream.readline()
        if not line:
            time.sleep(0.05)
            continue
        buffer.append(line)
        if pattern in line:
            return line


def free_tcp_port() -> int:
    """Return an available TCP port on localhost."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        _, port = sock.getsockname()
    return port
