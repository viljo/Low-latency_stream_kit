"""Headless TSPI producer that publishes to JetStream-compatible publishers."""
from __future__ import annotations

import asyncio
import inspect
import time
from datetime import datetime, timezone
from typing import Dict, Optional

import cbor2

from .datagrams import ParsedTSPI, parse_tspi_datagram
from .jetstream import build_subject, message_headers


class TSPIProducer:
    """Parse raw TSPI datagrams and publish CBOR payloads."""

    def __init__(self, publisher, *, stream_prefix: str = "tspi") -> None:
        self._publisher = publisher
        self._stream_prefix = stream_prefix

    def _encode_payload(self, parsed: ParsedTSPI, recv_time: float) -> Dict[str, object]:
        recv_epoch_ms = int(round(recv_time * 1000))
        recv_iso = datetime.fromtimestamp(recv_time, tz=timezone.utc).isoformat()
        payload: Dict[str, object] = {
            "type": parsed.type,
            "sensor_id": parsed.sensor_id,
            "day": parsed.day,
            "time_s": parsed.time_s,
            "status": parsed.status,
            "status_flags": parsed.status_flags,
            "recv_epoch_ms": recv_epoch_ms,
            "recv_iso": recv_iso,
            "payload": parsed.payload,
        }
        return payload

    def ingest(self, datagram: bytes, *, recv_time: Optional[float] = None) -> Dict[str, object]:
        parsed = parse_tspi_datagram(datagram)
        recv_time = recv_time if recv_time is not None else time.time()
        payload = self._encode_payload(parsed, recv_time)

        subject = build_subject(parsed, stream_prefix=self._stream_prefix)
        headers = message_headers(parsed)

        encoded = cbor2.dumps(payload)
        result = self._publisher.publish(
            subject, encoded, headers=headers, timestamp=recv_time
        )
        if inspect.isawaitable(result):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(result)
            else:
                asyncio.ensure_future(result)
        return payload
