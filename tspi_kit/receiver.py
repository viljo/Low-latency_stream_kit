"""Durable JetStream consumer utilities for integration tests."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Mapping, Sequence

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


class _PendingAggregator:
    """Expose a ``pending`` method that aggregates multiple consumers."""

    def __init__(self, consumers: Sequence[object]) -> None:
        self._consumers = consumers

    def pending(self) -> int:
        total = 0
        for consumer in self._consumers:
            if consumer is None:
                continue
            pending = getattr(consumer, "pending", None)
            if pending is None:
                continue
            try:
                value = pending()
            except TypeError:
                value = pending
            except Exception:  # pragma: no cover - defensive fallback
                continue
            try:
                total += int(value)
            except (TypeError, ValueError):  # pragma: no cover - defensive fallback
                continue
        return total


class CompositeTSPIReceiver:
    """Combine multiple receivers into a single ordered fetch interface."""

    def __init__(self, receivers: Iterable[TSPIReceiver]) -> None:
        receivers = list(receivers)
        if not receivers:
            raise ValueError("At least one receiver must be provided")
        self._receivers = receivers
        consumers = [getattr(receiver, "_consumer", None) for receiver in receivers]
        self._consumer = _PendingAggregator(consumers)
        self._sequence = 0

    @staticmethod
    def _extract_timestamp(message: Mapping[str, object]) -> float | None:
        epoch = message.get("recv_epoch_ms")
        if isinstance(epoch, (int, float)):
            return float(epoch) / 1000.0
        iso = message.get("recv_iso")
        if isinstance(iso, str) and iso:
            try:
                return datetime.fromisoformat(iso).timestamp()
            except ValueError:
                return None
        return None

    def fetch(self, batch: int = 1) -> List[dict]:
        annotated: List[tuple[float, int, dict]] = []
        for receiver in self._receivers:
            messages = receiver.fetch(batch)
            for message in messages:
                if isinstance(message, Mapping):
                    timestamp = self._extract_timestamp(message)
                else:
                    timestamp = None
                annotated.append(
                    (
                        timestamp if timestamp is not None else float(self._sequence),
                        self._sequence,
                        message,
                    )
                )
                self._sequence += 1
        annotated.sort(key=lambda item: (item[0], item[1]))
        return [message for _, _, message in annotated]

    def fetch_all(self, batch_size: int = 50) -> List[dict]:
        results: List[dict] = []
        while True:
            batch = self.fetch(batch_size)
            if not batch:
                break
            results.extend(batch)
        return results


__all__ = [
    "TSPIReceiver",
    "CompositeTSPIReceiver",
]
