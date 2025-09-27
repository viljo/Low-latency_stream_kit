"""Utilities for constructing and publishing TSPI command messages."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Dict

import cbor2

COMMAND_SUBJECT_PREFIX = "tspi.cmd.display"


@dataclass(slots=True)
class CommandPayload:
    """Structured representation of a display command."""

    cmd_id: str
    name: str
    ts: str
    sender: str
    payload: Dict[str, object]

    def to_dict(self) -> Dict[str, object]:
        return {
            "cmd_id": self.cmd_id,
            "name": self.name,
            "ts": self.ts,
            "sender": self.sender,
            "payload": dict(self.payload),
        }


class CommandSender:
    """Publish display commands to JetStream-compatible publishers."""

    def __init__(self, publisher, *, sender_id: str = "command-ui") -> None:
        self._publisher = publisher
        self._sender_id = sender_id

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(tz=UTC).isoformat()

    def _publish(self, subject: str, payload: CommandPayload) -> CommandPayload:
        encoded = cbor2.dumps(payload.to_dict())
        headers = {"Nats-Msg-Id": payload.cmd_id}
        result = self._publisher.publish(subject, encoded, headers=headers, timestamp=time.time())
        if result is False:
            raise RuntimeError(f"Failed to publish command {payload.cmd_id}")
        return payload

    def _build(self, name: str, body: Dict[str, object]) -> CommandPayload:
        return CommandPayload(
            cmd_id=str(uuid.uuid4()),
            name=name,
            ts=self._timestamp(),
            sender=self._sender_id,
            payload=body,
        )

    def send_units(self, units: str) -> CommandPayload:
        """Publish a units command."""

        normalized = units.lower()
        if normalized not in {"metric", "imperial"}:
            raise ValueError("Units must be 'metric' or 'imperial'")
        payload = self._build("display.units", {"units": normalized})
        subject = f"{COMMAND_SUBJECT_PREFIX}.units"
        return self._publish(subject, payload)

    def send_marker_color(self, color: str) -> CommandPayload:
        """Publish a marker color command."""

        if not color:
            raise ValueError("Color must be a non-empty string")
        normalized = color.strip()
        body = {"marker_color": normalized}
        payload = self._build("display.marker_color", body)
        subject = f"{COMMAND_SUBJECT_PREFIX}.marker_color"
        return self._publish(subject, payload)


__all__ = ["CommandSender", "COMMAND_SUBJECT_PREFIX", "CommandPayload"]
