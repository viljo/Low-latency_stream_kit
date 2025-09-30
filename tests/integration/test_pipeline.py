from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List

import cbor2
import jsonschema
import pytest

try:  # pragma: no cover - optional dependency for integration tests
    import nats
    from nats.errors import TimeoutError as NatsTimeoutError
except ImportError:  # pragma: no cover - skip when unavailable
    nats = None  # type: ignore[assignment]

    class NatsTimeoutError(Exception):
        pass

from .utils import free_tcp_port

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "tspi_kit" / "tspi.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)

if nats is None:  # pragma: no cover - dependency guard
    pytest.skip("nats-py is required for integration tests", allow_module_level=True)


@pytest.fixture
def log_buffer(request):
    logs: Dict[str, List[str]] = {}
    yield logs
    rep = getattr(request.node, "rep_call", None)
    if rep is not None and rep.failed:
        for name, lines in logs.items():
            tail = lines[-200:]
            header = f"--- {name} (last {len(tail)} lines) ---"
            sys.stderr.write(header + "\n")
            for entry in tail:
                sys.stderr.write(entry)
            sys.stderr.write("--- end ---\n")


@pytest.fixture(scope="session")
def nats_server():
    port = free_tcp_port()
    cmd = [
        "nats-server",
        "-js",
        "-p",
        str(port),
        "--server_name",
        "integration-tests",
        "--addr",
        "127.0.0.1",
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        pytest.skip("nats-server is required for integration tests")

    assert proc.stdout is not None
    start_time = time.monotonic()
    ready = False
    while time.monotonic() - start_time < 10:
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.1)
            continue
        if "Server is ready" in line:
            ready = True
            break
    if not ready:
        proc.kill()
        out, _ = proc.communicate(timeout=5)
        pytest.skip(f"Unable to start nats-server:\n{out}")

    url = f"nats://127.0.0.1:{port}"
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@pytest.fixture
def temp_logdir(tmp_path_factory):
    return tmp_path_factory.mktemp("integration-logs")


def _start_process(cmd: List[str], logs: Dict[str, List[str]], name: str, env: Dict[str, str] | None = None):
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=REPO_ROOT,
        env=merged_env,
    )
    assert proc.stdout is not None
    lines = logs.setdefault(name, [])

    def _reader() -> None:
        for output in iter(proc.stdout.readline, ""):
            lines.append(output)
        proc.stdout.close()

    thread = threading.Thread(target=_reader, name=f"capture-{name}", daemon=True)
    thread.start()
    return proc, thread


async def _fetch_tspi_messages(url: str, limit: int = 10) -> List[dict]:
    nc = await nats.connect(url)
    js = nc.jetstream()
    durable = f"integration-{uuid.uuid4().hex}"
    try:
        sub = await js.pull_subscribe("tspi.>", durable=durable, stream="TSPI")
    except Exception:
        await nc.close()
        return []

    collected: List[dict] = []
    try:
        while len(collected) < limit:
            try:
                batch = await sub.fetch(min(10, limit - len(collected)), timeout=1)
            except NatsTimeoutError:
                break
            for message in batch:
                try:
                    payload = cbor2.loads(message.data)
                except Exception:
                    await message.ack()
                    continue
                if isinstance(payload, dict) and "payload" in payload and "type" in payload:
                    collected.append(payload)
                await message.ack()
            if not batch:
                break
    finally:
        try:
            await js.delete_consumer("TSPI", durable)
        except Exception:
            pass
        await nc.close()
    return collected


def _parse_metrics(lines: List[str]) -> List[dict]:
    metrics: List[dict] = []
    for entry in lines:
        entry = entry.strip()
        if not entry:
            continue
        try:
            payload = json.loads(entry)
        except json.JSONDecodeError:
            continue
        if {"frames", "rate"}.issubset(payload):
            metrics.append(payload)
    return metrics


@pytest.mark.timeout(180)
def test_live_pipeline_generates_and_receives(nats_server, log_buffer, temp_logdir):
    player_cmd = [
        sys.executable,
        "player_flet.py",
        "--headless",
        "--source",
        "live",
        "--nats-server",
        nats_server,
        "--duration",
        "12",
        "--json-stream",
        "--exit-on-idle",
        "5",
    ]
    player_proc, player_thread = _start_process(player_cmd, log_buffer, "player")

    gen_cmd = [
        sys.executable,
        "tspi_generator_flet.py",
        "--headless",
        "--nats-server",
        nats_server,
        "--duration",
        "10",
        "--count",
        "3",
        "--rate",
        "20",
    ]
    generator_proc, generator_thread = _start_process(gen_cmd, log_buffer, "generator")

    try:
        generator_proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        generator_proc.kill()
        pytest.fail("Generator did not exit in time")
    finally:
        generator_thread.join(timeout=5)

    try:
        player_proc.wait(timeout=40)
    except subprocess.TimeoutExpired:
        player_proc.kill()
        pytest.fail("Player did not exit in time")
    finally:
        player_thread.join(timeout=5)

    player_metrics = _parse_metrics(log_buffer.get("player", []))
    assert player_metrics, "Player did not emit JSON metrics"
    assert max(metric["frames"] for metric in player_metrics) > 0

    frames = asyncio.run(_fetch_tspi_messages(nats_server, limit=10))
    assert frames, "No telemetry frames were stored in JetStream"
    for frame in frames:
        VALIDATOR.validate(frame)
        payload = frame.get("payload", {})
        assert isinstance(payload, dict)
        if frame["type"] == "geocentric":
            for key in ("x_m", "y_m", "z_m"):
                assert key in payload
        elif frame["type"] == "spherical":
            for key in ("range_m", "azimuth_deg", "elevation_deg"):
                assert key in payload


@pytest.mark.timeout(180)
def test_replay_channel_directory_and_join(nats_server, log_buffer, temp_logdir):
    pytest.skip("Replay channel discovery is not implemented in headless mode")


@pytest.mark.timeout(180)
def test_command_broadcast_units_switch(nats_server, log_buffer, temp_logdir):
    pytest.skip("Command broadcast verification requires player logging support")


@pytest.mark.timeout(180)
def test_tagg_annotation_live_then_replay(nats_server, log_buffer, temp_logdir):
    pytest.skip("Tag annotation replay is not available in the current build")
