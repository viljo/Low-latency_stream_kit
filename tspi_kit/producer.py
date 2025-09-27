"""Headless TSPI to JetStream producer that publishes to JetStream."""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Dict, Iterable, Optional, Set, Tuple

import time

import cbor2

from .datagrams import ParsedTSPI, parse_tspi_datagram
from .jetstream import build_subject, message_headers


class TSPIProducer:
    """Parse raw TSPI datagrams and publish CBOR payloads."""

    def __init__(
        self,
        publisher,
        *,
        stream_prefix: str = "tspi",
        allowed_sensors: Optional[Iterable[int]] = None,
    ) -> None:
        self._publisher = publisher
        self._stream_prefix = stream_prefix
        self._allowed_sensors: Optional[Set[int]] = (
            set(allowed_sensors) if allowed_sensors is not None else None
        )

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

    def _prepare_message(
        self, datagram: bytes, recv_time: float
    ) -> Optional[Tuple[str, Dict[str, str], bytes, Dict[str, object]]]:
        parsed = parse_tspi_datagram(datagram)

        if self._allowed_sensors is not None and parsed.sensor_id not in self._allowed_sensors:
            return None

        payload = self._encode_payload(parsed, recv_time)
        subject = build_subject(parsed, stream_prefix=self._stream_prefix)
        headers = message_headers(parsed)
        encoded = cbor2.dumps(payload)
        return subject, headers, encoded, payload

    def ingest(self, datagram: bytes, *, recv_time: Optional[float] = None) -> Optional[Dict[str, object]]:
        recv_time = recv_time if recv_time is not None else time.time()
        message = self._prepare_message(datagram, recv_time)
        if message is None:
            return None

        subject, headers, encoded, payload = message
        self._publisher.publish(subject, encoded, headers=headers, timestamp=recv_time)
        return payload

    async def ingest_async(
        self, datagram: bytes, *, recv_time: Optional[float] = None
    ) -> Optional[Dict[str, object]]:
        recv_time = recv_time if recv_time is not None else time.time()
        message = self._prepare_message(datagram, recv_time)
        if message is None:
            return None

        subject, headers, encoded, payload = message
        result = self._publisher.publish(
            subject,
            encoded,
            headers=headers,
            timestamp=recv_time,
        )
        if inspect.isawaitable(result):
            await result
        return payload
