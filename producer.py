"""Standalone UDP → JetStream TSPI producer."""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from typing import Iterable, Sequence

from nats.aio.client import Client as NATS

from tspi_kit.producer import TSPIProducer
from tspi_kit.udp_ingest import AsyncJetStreamPublisher, UDPIngestProtocol


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="UDP TSPI producer for JetStream")
    parser.add_argument(
        "--udp-host",
        default="0.0.0.0",
        help="UDP host/interface to bind (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--udp-port",
        type=int,
        default=30000,
        help="UDP port to listen on for TSPI datagrams (default: 30000)",
    )
    parser.add_argument(
        "--nats-server",
        action="append",
        dest="nats_servers",
        default=None,
        help="NATS JetStream server URL. Specify multiple times for failover.",
    )
    parser.add_argument(
        "--stream-prefix",
        default="tspi",
        help="Subject prefix used when publishing to JetStream (default: tspi)",
    )
    parser.add_argument(
        "--sensor-id",
        type=int,
        action="append",
        dest="sensor_ids",
        default=None,
        help="Only ingest datagrams from the specified sensor id. Repeatable.",
    )
    parser.add_argument(
        "--publish-timeout",
        type=float,
        default=2.0,
        help="Timeout (seconds) for JetStream publish operations.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"],
        help="Logging level for the producer (default: INFO)",
    )
    return parser


async def _run_async(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    nats_servers: Sequence[str] = (
        args.nats_servers if args.nats_servers is not None else ["nats://127.0.0.1:4222"]
    )

    logging.info("Connecting to NATS JetStream at %s", ", ".join(nats_servers))
    nc = NATS()
    await nc.connect(servers=list(nats_servers))
    js = nc.jetstream()

    publisher = AsyncJetStreamPublisher(js, publish_timeout=args.publish_timeout)
    allowed_sensors: Iterable[int] | None = (
        set(args.sensor_ids) if args.sensor_ids is not None else None
    )
    producer = TSPIProducer(
        publisher,
        stream_prefix=args.stream_prefix,
        allowed_sensors=allowed_sensors,
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:  # pragma: no cover - Windows fallback
            pass

    transport, protocol = await loop.create_datagram_endpoint(
        lambda: UDPIngestProtocol(producer, loop=loop),
        local_addr=(args.udp_host, args.udp_port),
    )

    try:
        await stop_event.wait()
    finally:
        transport.close()
        await protocol.drain()
        await nc.drain()
        await nc.close()

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run_async(args))
    except KeyboardInterrupt:  # pragma: no cover - handled via signal but for safety
        return 130


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
