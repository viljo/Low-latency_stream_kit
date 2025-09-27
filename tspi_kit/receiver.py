"""Durable JetStream consumer utilities for integration tests."""
from __future__ import annotations

from typing import List, Mapping

import cbor2

from .schema import validate_payload


class TSPIReceiver:
    """Pull messages from JetStream and decode CBOR payloads."""

    def __init__(self, consumer, *, validate: bool = True) -> None:
        self._consumer = consumer
        self._validate = validate

    @staticmethod
    def _is_telemetry(payload: Mapping[str, object]) -> bool:
        return "type" in payload and "sensor_id" in payload and "cmd_id" not in payload

    def fetch(self, batch: int = 1) -> List[dict]:
        messages = self._consumer.pull(batch)
        decoded: List[dict] = []
        for message in messages:
            payload = cbor2.loads(message.data)
            if self._validate and isinstance(payload, Mapping) and self._is_telemetry(payload):
                validate_payload(payload)
            decoded.append(payload)
        return decoded

    def fetch_all(self, batch_size: int = 50) -> List[dict]:
        results: List[dict] = []
        while True:
            batch = self.fetch(batch_size)
            if not batch:
                break
            results.extend(batch)
        return results
