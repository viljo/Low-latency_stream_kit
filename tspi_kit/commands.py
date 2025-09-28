"""Utilities for constructing and publishing TSPI command messages."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Dict, Optional

import cbor2

from .channels import (
    TSPI_STREAM,
    GroupReplayStartMessage,
    GroupReplayStopMessage,
    group_replay_channel,
)


COMMAND_SUBJECT_PREFIX = "tspi.cmd.display"
OPS_CONTROL_SUBJECT = "tspi.ops.ctrl"


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

    @property
    def sender_id(self) -> str:
        return self._sender_id

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

    def send_session_metadata(self, name: str, identifier: str) -> CommandPayload:
        """Broadcast operator-selected session metadata to all receivers."""

        if not isinstance(name, str) or not name.strip():
            raise ValueError("Session name must be a non-empty string")
        if not isinstance(identifier, str):
            identifier = str(identifier)
        if not identifier.strip():
            raise ValueError("Session identifier must be a non-empty string")

        normalized_name = name.strip()
        normalized_id = identifier.strip()
        body = {"session_metadata": {"name": normalized_name, "id": normalized_id}}
        payload = self._build("display.session_metadata", body)
        subject = f"{COMMAND_SUBJECT_PREFIX}.session_metadata"
        return self._publish(subject, payload)


class OpsControlSender:
    """Publish operations control messages such as group replay broadcasts."""

    def __init__(self, publisher, *, sender_id: str = "command-ui") -> None:
        self._publisher = publisher
        self._sender_id = sender_id
        self._active_channel_id: Optional[str] = None

    def _publish(self, payload: Dict[str, object], *, message_id: Optional[str] = None) -> Dict[str, object]:
        headers = {"X-Command-Sender": self._sender_id}
        if message_id:
            headers["Nats-Msg-Id"] = message_id
        encoded = cbor2.dumps(payload)
        result = self._publisher.publish(
            OPS_CONTROL_SUBJECT,
            encoded,
            headers=headers,
            timestamp=time.time(),
        )
        if result is False:
            raise RuntimeError("Failed to publish operations command")
        return payload

    def start_group_replay(
        self,
        identifier,
        *,
        stream: str = TSPI_STREAM,
        display_name: Optional[str] = None,
    ) -> GroupReplayStartMessage:
        channel = group_replay_channel(identifier, stream=stream, display_name=display_name)
        message = GroupReplayStartMessage(channel)
        message_id = f"{channel.channel_id}:start:{uuid.uuid4()}"
        self._publish(message.to_dict(), message_id=message_id)
        self._active_channel_id = channel.channel_id
        return message

    def stop_group_replay(self, channel_id: Optional[str] = None) -> GroupReplayStopMessage:
        resolved = channel_id or self._active_channel_id
        if not resolved:
            raise ValueError("Group replay channel_id is required to stop a replay")
        message = GroupReplayStopMessage(resolved)
        message_id = f"{resolved}:stop:{uuid.uuid4()}"
        self._publish(message.to_dict(), message_id=message_id)
        if self._active_channel_id == resolved:
            self._active_channel_id = None
        return message

    @property
    def active_channel_id(self) -> Optional[str]:
        return self._active_channel_id


__all__ = [
    "CommandSender",
    "COMMAND_SUBJECT_PREFIX",
    "CommandPayload",
    "OpsControlSender",
    "OPS_CONTROL_SUBJECT",
]
