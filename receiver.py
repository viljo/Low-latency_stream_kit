#!/usr/bin/env python3
"""JetStream TSPI receiver entry point."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import signal
import time
from typing import Optional

import cbor2

from tspi_kit.schema import validate_payload

try:  # pragma: no cover - optional dependency path
    from nats.aio.client import Client as NATS
    from nats.js.errors import TimeoutError
except ModuleNotFoundError:  # pragma: no cover - exercised when nats-py missing
    NATS = None  # type: ignore[assignment]
    TimeoutError = Exception  # type: ignore[assignment]


LOGGER = logging.getLogger("tspi.receiver")


async def _consume(
    js,
    stream: str,
    subject: str,
    durable: str,
    batch: int,
    validate: bool,
    json_output: bool,
    metrics_interval: float,
    exit_on_idle: Optional[float],
) -> None:
    subscription = await js.pull_subscribe(subject, durable=durable, stream=stream)
    last_metrics = time.monotonic()
    last_activity = time.monotonic()
    frames = 0

    async def _emit_metrics() -> None:
        LOGGER.info("frames=%s", frames)

    while True:
        try:
            messages = await subscription.fetch(batch, timeout=metrics_interval)
        except TimeoutError:
            if exit_on_idle is not None and time.monotonic() - last_activity >= exit_on_idle:
                LOGGER.info("exit-on-idle threshold reached")
                break
            await _emit_metrics()
            continue

        for message in messages:
            payload = cbor2.loads(message.data)
            if validate:
                validate_payload(payload)
            if json_output:
                print(json.dumps(payload))
            else:
                print(payload)
            await message.ack()
            frames += 1
            last_activity = time.monotonic()

        if time.monotonic() - last_metrics >= metrics_interval:
            await _emit_metrics()
            last_metrics = time.monotonic()


async def _main_async(args: argparse.Namespace) -> int:
    if NATS is None:
        raise SystemExit("nats-py is required to run the receiver")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:  # pragma: no cover - platform specific
            signal.signal(sig, lambda *_: _stop())

    nc = NATS()
    await nc.connect(servers=args.nats_server)
    js = nc.jetstream()

    consumer_task = asyncio.create_task(
        _consume(
            js,
            stream=args.stream,
            subject=args.subject,
            durable=args.durable,
            batch=args.batch,
            validate=not args.no_validate,
            json_output=args.json,
            metrics_interval=args.metrics_interval,
            exit_on_idle=args.exit_on_idle,
        )
    )

    stop_wait = asyncio.create_task(stop_event.wait())
    done, pending = await asyncio.wait(
        {consumer_task, stop_wait}, return_when=asyncio.FIRST_COMPLETED
    )

    if stop_wait in done:
        consumer_task.cancel()
    else:
        stop_event.set()
    for task in pending:
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await consumer_task
    with contextlib.suppress(asyncio.CancelledError):
        await stop_wait
    await nc.drain()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BAPS TSPI JetStream receiver")
    parser.add_argument("--nats-server", default="nats://127.0.0.1:4222", help="NATS server URL")
    parser.add_argument("--stream", default="TSPI", help="JetStream name")
    parser.add_argument("--subject", default="tspi.>", help="Subject filter to consume")
    parser.add_argument("--durable", default="tspi-receiver", help="Durable consumer name")
    parser.add_argument("--batch", type=int, default=50, help="Pull batch size")
    parser.add_argument("--metrics-interval", type=float, default=5.0, help="Metrics emission interval")
    parser.add_argument("--exit-on-idle", type=float, help="Stop after seconds of inactivity")
    parser.add_argument("--json", action="store_true", help="Emit JSON payloads to stdout")
    parser.add_argument("--no-validate", action="store_true", help="Disable JSON schema validation")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


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
