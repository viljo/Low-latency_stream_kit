"""Channel and replay management primitives for JetStream deployments.

This module implements the requirements captured in
``docs/channels-replay-spec.md``.  It provides helpers for generating the
channel subjects described in the spec, utilities for advertising active
channels, JSON serialisable control messages, and structures for tracking the
client state machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, MutableMapping, Optional

TSPI_STREAM = "TSPI"
"""Primary JetStream stream that contains live telemetry."""

TSPI_REPLAY_STREAM = "TSPI_REPLAY"
"""Short-retention stream used for advertising replay channels."""

LIVESTREAM_SUBJECT = "tspi.channel.livestream"
"""Subject used for the default live channel fan-out."""

REPLAY_SUBJECT_PREFIX = "tspi.channel.replay"
"""Prefix used for group replay channel subjects."""

CLIENT_SUBJECT_PREFIX = "tspi.channel.client"
"""Prefix used for private client replay channel subjects."""


class ChannelKind(str, Enum):
    """Different categories of channels exposed to clients."""

    LIVESTREAM = "livestream"
    GROUP_REPLAY = "group_replay"
    PRIVATE_REPLAY = "private_replay"


class ClientState(str, Enum):
    """Client playback states defined by the channel specification."""

    FOLLOWING_LIVESTREAM = "FOLLOWING_LIVESTREAM"
    FOLLOWING_GROUP_REPLAY = "FOLLOWING_GROUP_REPLAY"
    FOLLOWING_PRIVATE_REPLAY = "FOLLOWING_PRIVATE_REPLAY"
    LIVE_OVERRIDE = "LIVE_OVERRIDE"


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_timestamp(value: datetime | str | float) -> datetime:
    if isinstance(value, datetime):
        return _ensure_utc(value)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("Timestamp string may not be empty")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:  # pragma: no cover - defensive fallback
            raise ValueError(f"Invalid ISO timestamp: {value!r}") from exc
        return _ensure_utc(parsed)
    raise TypeError("Unsupported timestamp type; expected datetime, str, or float")


def _isoformat(dt: datetime) -> str:
    return _ensure_utc(dt).isoformat(timespec="seconds").replace("+00:00", "Z")


def _channel_suffix(dt: datetime) -> str:
    return _ensure_utc(dt).strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True)
class ChannelDescriptor:
    """Description for a discoverable playback channel."""

    channel_id: str
    subject: str
    display_name: str
    kind: ChannelKind
    stream: str = TSPI_STREAM
    start: Optional[str] = None
    end: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        data: Dict[str, object] = {
            "channel_id": self.channel_id,
            "subject": self.subject,
            "display_name": self.display_name,
            "kind": self.kind.value,
            "stream": self.stream,
        }
        if self.start is not None:
            data["start"] = self.start
        if self.end is not None:
            data["end"] = self.end
        return data


def live_channel() -> ChannelDescriptor:
    """Return the descriptor for the always-on livestream channel."""

    return ChannelDescriptor(
        channel_id="livestream",
        subject=LIVESTREAM_SUBJECT,
        display_name="livestream",
        kind=ChannelKind.LIVESTREAM,
    )


def group_replay_channel(
    start: datetime | str | float,
    *,
    end: datetime | str | float | None = None,
    stream: str = TSPI_STREAM,
) -> ChannelDescriptor:
    """Return the descriptor for a group replay channel.

    Parameters
    ----------
    start:
        Starting timestamp for the replay window.
    end:
        Optional end timestamp if the operator pre-computed the replay window.
    stream:
        JetStream stream backing the replay. Defaults to ``TSPI``.
    """

    start_dt = _parse_timestamp(start)
    start_iso = _isoformat(start_dt)
    channel_suffix = _channel_suffix(start_dt)
    end_iso = _isoformat(_parse_timestamp(end)) if end is not None else None
    return ChannelDescriptor(
        channel_id=f"replay.{channel_suffix}",
        subject=f"{REPLAY_SUBJECT_PREFIX}.{channel_suffix}",
        display_name=f"replay {start_iso}",
        kind=ChannelKind.GROUP_REPLAY,
        stream=stream,
        start=start_iso,
        end=end_iso,
    )


def private_channel(
    client_id: str,
    session_id: str,
    *,
    stream: str = TSPI_STREAM,
) -> ChannelDescriptor:
    """Return the descriptor for a private client replay channel."""

    client_id = client_id.strip()
    session_id = session_id.strip()
    if not client_id or not session_id:
        raise ValueError("client_id and session_id must be non-empty strings")
    return ChannelDescriptor(
        channel_id=f"client.{client_id}.{session_id}",
        subject=f"{CLIENT_SUBJECT_PREFIX}.{client_id}.{session_id}",
        display_name=f"client {client_id}/{session_id}",
        kind=ChannelKind.PRIVATE_REPLAY,
        stream=stream,
    )


@dataclass(frozen=True)
class GroupReplayStartMessage:
    """Representation of the ``GroupReplayStart`` control broadcast."""

    channel: ChannelDescriptor

    def to_dict(self) -> Dict[str, object]:
        payload = {
            "type": "GroupReplayStart",
            "channel_id": self.channel.channel_id,
            "display_name": self.channel.display_name,
            "start": self.channel.start,
            "stream": self.channel.stream,
        }
        if self.channel.end is not None:
            payload["end"] = self.channel.end
        return payload


@dataclass(frozen=True)
class GroupReplayStopMessage:
    """Representation of the ``GroupReplayStop`` control broadcast."""

    channel_id: str

    def to_dict(self) -> Dict[str, object]:
        return {"type": "GroupReplayStop", "channel_id": self.channel_id}


@dataclass
class ChannelStatus:
    """Heartbeat payload that announces a client's current channel."""

    client_id: str
    state: ClientState
    channel: ChannelDescriptor
    override: bool = False
    timestamp: datetime | str | float = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, object]:
        ts_iso = _isoformat(_parse_timestamp(self.timestamp))
        return {
            "client_id": self.client_id,
            "state": self.state.value,
            "channel_id": self.channel.channel_id,
            "subject": self.channel.subject,
            "override": bool(self.override),
            "ts": ts_iso,
        }


class ChannelDirectory:
    """Registry that exposes currently discoverable channels."""

    _SORT_ORDER = {
        ChannelKind.GROUP_REPLAY: 0,
        ChannelKind.PRIVATE_REPLAY: 1,
    }

    def __init__(self) -> None:
        self._channels: MutableMapping[str, ChannelDescriptor] = {
            "livestream": live_channel()
        }

    def upsert(self, channel: ChannelDescriptor, *, advertise: bool = True) -> None:
        if channel.kind is ChannelKind.PRIVATE_REPLAY and not advertise:
            return
        self._channels[channel.channel_id] = channel

    def remove(self, channel_id: str) -> None:
        if channel_id == "livestream":
            return
        self._channels.pop(channel_id, None)

    def list_channels(self, *, include_private: bool = True) -> List[ChannelDescriptor]:
        entries = [self._channels["livestream"]]
        others = [
            channel
            for key, channel in self._channels.items()
            if key != "livestream"
        ]
        others.sort(
            key=lambda item: (
                self._SORT_ORDER.get(item.kind, 99),
                item.channel_id,
            )
        )
        for channel in others:
            if channel.kind is ChannelKind.PRIVATE_REPLAY and not include_private:
                continue
            entries.append(channel)
        return entries

    def to_dicts(self, *, include_private: bool = True) -> List[Dict[str, object]]:
        return [channel.to_dict() for channel in self.list_channels(include_private=include_private)]


class ChannelManager:
    """High-level helper coordinating group and private channel lifecycles."""

    def __init__(self, directory: ChannelDirectory | None = None) -> None:
        self._directory = directory or ChannelDirectory()
        self._active_group: Optional[str] = None

    @property
    def directory(self) -> ChannelDirectory:
        return self._directory

    def start_group_replay(
        self,
        start: datetime | str | float,
        *,
        end: datetime | str | float | None = None,
        stream: str = TSPI_STREAM,
    ) -> GroupReplayStartMessage:
        channel = group_replay_channel(start, end=end, stream=stream)
        self._directory.upsert(channel)
        self._active_group = channel.channel_id
        return GroupReplayStartMessage(channel)

    def stop_group_replay(self, channel_id: str | None = None) -> GroupReplayStopMessage | None:
        if channel_id is None:
            channel_id = self._active_group
        if not channel_id:
            return None
        channel = self._directory._channels.get(channel_id)
        if channel is None or channel.kind is not ChannelKind.GROUP_REPLAY:
            return None
        self._directory.remove(channel_id)
        if self._active_group == channel_id:
            self._active_group = None
        return GroupReplayStopMessage(channel_id)

    def register_private_channel(
        self,
        client_id: str,
        session_id: str,
        *,
        advertise: bool = True,
        stream: str = TSPI_STREAM,
    ) -> ChannelDescriptor:
        channel = private_channel(client_id, session_id, stream=stream)
        self._directory.upsert(channel, advertise=advertise)
        return channel

    def remove_private_channel(self, client_id: str, session_id: str) -> None:
        channel_id = f"client.{client_id.strip()}.{session_id.strip()}"
        self._directory.remove(channel_id)


def live_consumer_config() -> Dict[str, object]:
    """Return the JetStream configuration for the shared live consumer."""

    return {
        "stream": TSPI_STREAM,
        "durable_name": "LIVE_MAIN",
        "deliver_subject": LIVESTREAM_SUBJECT,
        "deliver_policy": "deliver_new",
        "ack_policy": "none",
        "flow_control": True,
        "idle_heartbeat": True,
    }


def replay_consumer_config(channel: ChannelDescriptor) -> Dict[str, object]:
    """Return the JetStream configuration for a replay channel consumer."""

    if channel.kind not in {ChannelKind.GROUP_REPLAY, ChannelKind.PRIVATE_REPLAY}:
        raise ValueError("Replay consumer config requires a replay channel descriptor")
    config = {
        "stream": channel.stream,
        "deliver_subject": channel.subject,
        "deliver_policy": "by_start_time" if channel.start else "deliver_new",
        "replay_policy": "original",
        "ack_policy": "none",
        "flow_control": True,
        "idle_heartbeat": True,
    }
    if channel.kind is ChannelKind.GROUP_REPLAY:
        config["description"] = f"Group replay {channel.channel_id}"
    else:
        config["inactive_threshold"] = 120
    return config


def replay_advertisement_subjects() -> List[str]:
    """Subjects that should be persisted in the ``TSPI_REPLAY`` stream."""

    return [f"{REPLAY_SUBJECT_PREFIX}.>", f"{CLIENT_SUBJECT_PREFIX}.>"]


__all__ = [
    "TSPI_STREAM",
    "TSPI_REPLAY_STREAM",
    "LIVESTREAM_SUBJECT",
    "REPLAY_SUBJECT_PREFIX",
    "CLIENT_SUBJECT_PREFIX",
    "ChannelKind",
    "ClientState",
    "ChannelDescriptor",
    "ChannelDirectory",
    "ChannelManager",
    "GroupReplayStartMessage",
    "GroupReplayStopMessage",
    "ChannelStatus",
    "live_channel",
    "group_replay_channel",
    "private_channel",
    "live_consumer_config",
    "replay_consumer_config",
    "replay_advertisement_subjects",
]

