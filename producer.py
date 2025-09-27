#!/usr/bin/env python3
"""UDP → JetStream TSPI producer entry point."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Callable, Optional

from tspi_kit import TSPIProducer
from tspi_kit.jetstream_sim import InMemoryJetStream

try:  # pragma: no cover - optional dependency path
    from nats.aio.client import Client as NATS
    from nats.js.api import StreamConfig
    from nats.js.errors import NotFoundError
except ModuleNotFoundError:  # pragma: no cover - exercised when nats-py missing
    NATS = None  # type: ignore[assignment]
    StreamConfig = None  # type: ignore[assignment]
    NotFoundError = Exception  # type: ignore[assignment]


LOGGER = logging.getLogger("tspi.producer")


@dataclass
class Metrics:
    """Runtime counters for operator feedback."""

    frames: int = 0
    dropped: int = 0


class ProducerProtocol(asyncio.DatagramProtocol):
    """Dispatch incoming datagrams to the TSPI producer."""

    def __init__(self, handler: Callable[[bytes], None], metrics: Metrics) -> None:
        self._handler = handler
        self._metrics = metrics

    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[override]
        if len(data) != 37:
            self._metrics.dropped += 1
            LOGGER.debug("ignored non-TSPI payload from %s", addr)
            return
        try:
            self._handler(data)
        except Exception:  # pragma: no cover - logged for operator visibility
            self._metrics.dropped += 1
            LOGGER.exception("Failed to ingest TSPI datagram from %s", addr)
        else:
            self._metrics.frames += 1


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def _ensure_stream(js, stream: str, subject_prefix: str) -> None:
    subjects = [f"{subject_prefix}.>"]
    try:
        await js.stream_info(stream)
    except NotFoundError:
        config = StreamConfig(name=stream, subjects=subjects)
        await js.add_stream(config=config)


class JetStreamPublisher:
    """Adapter exposing the synchronous publish interface expected by TSPIProducer."""

    def __init__(self, js, *, stream: str, subject_prefix: str) -> None:
        _ = stream, subject_prefix
        self._js = js

    async def _publish(self, subject: str, payload: bytes, headers: Optional[dict]) -> None:
        await self._js.publish(subject, payload, headers=headers)

    def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        headers: Optional[dict] = None,
        timestamp: Optional[float] = None,
    ) -> Awaitable[None]:
        _ = timestamp  # timestamp already encoded into payload metadata
        return self._publish(subject, payload, headers)


async def _run_udp_server(
    producer: TSPIProducer,
    host: str,
    port: int,
    metrics: Metrics,
    stop_event: asyncio.Event,
) -> None:
    loop = asyncio.get_running_loop()
    protocol = ProducerProtocol(producer.ingest, metrics)
    transport, _ = await loop.create_datagram_endpoint(lambda: protocol, local_addr=(host, port))
    LOGGER.info("listening for TSPI datagrams on %s:%s", host, port)
    try:
        await stop_event.wait()
    finally:
        transport.close()
        LOGGER.info("UDP listener closed")


async def _log_metrics(metrics: Metrics, interval: float, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        await asyncio.sleep(interval)
        LOGGER.info("frames=%s dropped=%s", metrics.frames, metrics.dropped)


async def _main_async(args: argparse.Namespace) -> int:
    metrics = Metrics()
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()

    def _stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:  # pragma: no cover - platform specific
            signal.signal(sig, lambda *_: _stop())

    nc: Optional[NATS] = None
    if args.in_memory:
        publisher = InMemoryJetStream()
    else:
        if NATS is None:
            raise SystemExit("nats-py is required unless --in-memory is used")
        nc = NATS()
        await nc.connect(servers=args.nats_server)
        js = nc.jetstream()
        await _ensure_stream(js, args.stream, args.subject_prefix)
        publisher = JetStreamPublisher(js, stream=args.stream, subject_prefix=args.subject_prefix)

    producer = TSPIProducer(publisher, stream_prefix=args.subject_prefix)
    metrics_task = asyncio.create_task(_log_metrics(metrics, args.metrics_interval, stop_event))

    if args.duration:
        asyncio.get_running_loop().call_later(args.duration, stop_event.set)

    try:
        await _run_udp_server(producer, args.udp_host, args.udp_port, metrics, stop_event)
    finally:
        metrics_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await metrics_task
        if nc is not None:
            await nc.drain()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BAPS TSPI UDP producer")
    parser.add_argument("--udp-host", default="0.0.0.0", help="UDP bind address")
    parser.add_argument("--udp-port", type=int, default=30000, help="UDP port to ingest datagrams")
    parser.add_argument("--nats-server", default="nats://127.0.0.1:4222", help="NATS server URL")
    parser.add_argument("--stream", default="TSPI", help="JetStream name")
    parser.add_argument("--subject-prefix", default="tspi", help="Subject prefix for publications")
    parser.add_argument("--duration", type=float, help="Optional run duration in seconds")
    parser.add_argument("--metrics-interval", type=float, default=5.0, help="Metrics emission interval")
    parser.add_argument("--in-memory", action="store_true", help="Use the in-memory JetStream simulator")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _configure_logging(args.verbose)
    try:
        return asyncio.run(_main_async(args))
    except KeyboardInterrupt:  # pragma: no cover - handled for operator convenience
        LOGGER.info("Interrupted by user")
        return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
