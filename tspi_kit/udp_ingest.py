"""Async UDP ingestion helpers for the standalone TSPI producer."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional


class UDPIngestProtocol(asyncio.DatagramProtocol):
    """Receive UDP datagrams and hand them to a TSPI producer."""

    def __init__(self, producer, *, loop: Optional[asyncio.AbstractEventLoop] = None, logger=None) -> None:
        self._producer = producer
        self._loop = loop or asyncio.get_running_loop()
        self._logger = logger or logging.getLogger(__name__)
        self._pending: set[asyncio.Task[None]] = set()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:  # pragma: no cover - logging only
        sockname = transport.get_extra_info("sockname")
        self._logger.info("Listening for TSPI datagrams on %s", sockname)

    def connection_lost(self, exc: Optional[Exception]) -> None:  # pragma: no cover - logging only
        if exc:
            self._logger.error("UDP listener stopped due to error: %s", exc)
        else:
            self._logger.info("UDP listener stopped")

    def error_received(self, exc: Exception) -> None:  # pragma: no cover - logging only
        self._logger.warning("UDP socket reported error: %s", exc)

    def datagram_received(self, data: bytes, addr) -> None:
        recv_time = time.time()
        task = self._loop.create_task(self._ingest(data, recv_time))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _ingest(self, datagram: bytes, recv_time: float) -> None:
        try:
            await self._producer.ingest_async(datagram, recv_time=recv_time)
        except Exception:  # pragma: no cover - defensive logging
            self._logger.exception("Failed to ingest TSPI datagram")

    async def drain(self) -> None:
        if not self._pending:
            return

        await asyncio.gather(*self._pending, return_exceptions=True)
        self._pending.clear()


class AsyncJetStreamPublisher:
    """Adapter that proxies publish calls to an async JetStream context."""

    def __init__(self, jetstream, *, publish_timeout: float = 2.0) -> None:
        self._jetstream = jetstream
        self._publish_timeout = publish_timeout

    async def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        headers: Optional[dict[str, str]] = None,
        timestamp: Optional[float] = None,  # noqa: ARG002 - kept for compatibility
    ) -> None:
        await self._jetstream.publish(
            subject,
            payload,
            headers=headers,
            timeout=self._publish_timeout,
        )
