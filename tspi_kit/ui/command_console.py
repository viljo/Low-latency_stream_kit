from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cbor2


@dataclass(frozen=True)
class OperatorEvent:
    """Structured representation of an operator-facing event."""

    timestamp: datetime
    message: str


@dataclass(frozen=True)
class ClientPresence:
    """Snapshot describing an active client."""

    client_id: str
    channel_id: str
    subject: str | None
    state: str
    connection_ts: datetime
    last_seen_ts: datetime
    operator: str | None
    ping_ms: float | None
    source_ip: str | None

    @property
    def channel_display(self) -> str:
        if self.channel_id:
            return self.channel_id
        if self.subject:
            return self.subject
        return "unknown"

    @property
    def state_display(self) -> str:
        if not self.state:
            return ""
        return self.state.replace("_", " ").title()


@dataclass(frozen=True)
class DataChunkTag:
    """Represents a named timestamp within a datastore recording."""

    label: str
    timestamp: datetime

    def __post_init__(self) -> None:
        clean_label = str(self.label).strip()
        if not clean_label:
            raise ValueError("Tag label must be a non-empty string")
        object.__setattr__(self, "label", clean_label)
        normalized = _parse_timestamp(self.timestamp)
        object.__setattr__(self, "timestamp", normalized)

    def isoformat(self) -> str:
        return _isoformat(self.timestamp)


@dataclass(frozen=True)
class DataChunk:
    """Metadata describing a replayable datastore recording."""

    identifier: str
    start: datetime
    end: datetime
    display_name: Optional[str] = None
    tags: Sequence[DataChunkTag] = ()

    def __post_init__(self) -> None:
        ident = str(self.identifier).strip()
        if not ident:
            raise ValueError("Data chunk identifier must be a non-empty string")
        object.__setattr__(self, "identifier", ident)
        start_ts = _parse_timestamp(self.start)
        end_ts = _parse_timestamp(self.end)
        if end_ts < start_ts:
            end_ts = start_ts
        object.__setattr__(self, "start", start_ts)
        object.__setattr__(self, "end", end_ts)
        if self.display_name is not None:
            object.__setattr__(self, "display_name", str(self.display_name).strip())
        sorted_tags = tuple(sorted((DataChunkTag(tag.label, tag.timestamp) for tag in self.tags), key=lambda tag: tag.timestamp))
        object.__setattr__(self, "tags", sorted_tags)

    @property
    def label(self) -> str:
        return self.display_name or self.identifier

    @property
    def duration_seconds(self) -> int:
        return max(0, int((self.end - self.start).total_seconds()))

    def timestamp_at_offset(self, offset_seconds: int) -> datetime:
        offset = max(0, offset_seconds)
        clamped = min(offset, self.duration_seconds)
        return self.start + timedelta(seconds=clamped)

    def offset_for_timestamp(self, timestamp: datetime) -> int:
        ts = _parse_timestamp(timestamp)
        if ts <= self.start:
            return 0
        if ts >= self.end:
            return self.duration_seconds
        return int((ts - self.start).total_seconds())

    def first_tag(self) -> Optional[DataChunkTag]:
        return self.tags[0] if self.tags else None

    def find_tag(self, label: str) -> Optional[DataChunkTag]:
        cleaned = str(label).strip().lower()
        for tag in self.tags:
            if tag.label.lower() == cleaned:
                return tag
        return None


def _isoformat(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def compose_replay_identifier(
    chunk: DataChunk,
    *,
    start_time: datetime,
    tag: DataChunkTag | None = None,
) -> Tuple[str, str]:
    """Return a replay identifier and display label for the selected chunk/time."""

    base = chunk.identifier
    if tag is not None:
        identifier = f"{base} {tag.label}".strip()
        display = f"{chunk.label} — {tag.label}".strip()
    else:
        ts_iso = _isoformat(start_time)
        identifier = f"{base} {ts_iso}".strip()
        display = f"{chunk.label} @ {ts_iso}".strip()
    return identifier, display


def decode_status_payload(raw: bytes | Dict[str, Any] | None) -> Dict[str, Any] | None:
    """Decode a JetStream payload into a status dictionary."""

    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        payload = cbor2.loads(raw)
    except Exception:  # pragma: no cover - defensive against malformed payloads
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z") and "+" not in text:
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return datetime.now(tz=UTC)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return datetime.now(tz=UTC)


def _normalize_state(state: Any) -> str:
    if not isinstance(state, str):
        return ""
    return state.strip().upper()


def _extract_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _choose_first(*values: Any) -> Optional[str]:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class ClientPresenceTracker:
    """Track client statuses and derive operator-facing events."""

    def __init__(self) -> None:
        self._clients: Dict[str, ClientPresence] = {}
        self._order: List[str] = []

    def snapshot(self) -> List[ClientPresence]:
        return [self._clients[client_id] for client_id in self._order]

    def process_raw(self, raw: bytes | Dict[str, Any] | None) -> Tuple[ClientPresence | None, List[OperatorEvent]]:
        payload = decode_status_payload(raw)
        if payload is None:
            return None, []
        return self.process_payload(payload)

    def process_payload(self, payload: Dict[str, Any]) -> Tuple[ClientPresence | None, List[OperatorEvent]]:
        client_id = _choose_first(payload.get("client_id"), payload.get("client"))
        if not client_id:
            return None, []
        timestamp = _parse_timestamp(payload.get("ts"))
        state = _normalize_state(payload.get("state"))
        channel_id = _choose_first(payload.get("channel_id")) or ""
        subject = _choose_first(payload.get("subject"))
        operator = _choose_first(payload.get("operator"), payload.get("operator_login"), payload.get("username"))
        source_ip = _choose_first(payload.get("source_ip"), payload.get("remote_ip"), payload.get("ip"))
        ping_ms = _extract_optional_float(payload.get("ping_ms") or payload.get("ping"))
        override = bool(payload.get("override"))

        previous = self._clients.get(client_id)
        events: List[OperatorEvent] = []

        if previous is None:
            connection_ts = timestamp
            presence = ClientPresence(
                client_id=client_id,
                channel_id=channel_id,
                subject=subject,
                state=state,
                connection_ts=connection_ts,
                last_seen_ts=timestamp,
                operator=operator,
                ping_ms=ping_ms,
                source_ip=source_ip,
            )
            self._clients[client_id] = presence
            self._order.append(client_id)
            message = f"New client connected"
            if source_ip:
                message += f" from IP {source_ip}"
            message += f": {client_id}"
            events.append(OperatorEvent(timestamp=connection_ts, message=message))
            previous = presence
        else:
            connection_ts = previous.connection_ts

        updated = replace(
            previous,
            channel_id=channel_id or previous.channel_id,
            subject=subject or previous.subject,
            state=state or previous.state,
            last_seen_ts=timestamp,
            operator=operator if operator is not None else previous.operator,
            ping_ms=ping_ms if ping_ms is not None else previous.ping_ms,
            source_ip=source_ip if source_ip is not None else previous.source_ip,
        )
        self._clients[client_id] = updated

        if previous is not None and previous is not updated:
            events.extend(self._derive_state_events(previous, updated, timestamp, override))

        return updated, events

    def _derive_state_events(
        self,
        previous: ClientPresence,
        updated: ClientPresence,
        timestamp: datetime,
        override: bool,
    ) -> List[OperatorEvent]:
        events: List[OperatorEvent] = []
        if previous.state != updated.state:
            channel_display = updated.channel_display
            if updated.state.startswith("FOLLOWING_GROUP_REPLAY") or updated.state.startswith("FOLLOWING_PRIVATE_REPLAY"):
                message = f"Client {updated.client_id} started replay on {channel_display}"
                events.append(OperatorEvent(timestamp=timestamp, message=message))
            elif updated.state in {"FOLLOWING_LIVESTREAM", "LIVE_OVERRIDE"}:
                if override:
                    message = f"Client {updated.client_id} initiated live override"
                else:
                    message = f"Client {updated.client_id} resumed live view"
                events.append(OperatorEvent(timestamp=timestamp, message=message))
        return events


__all__ = [
    "ClientPresence",
    "ClientPresenceTracker",
    "DataChunk",
    "DataChunkTag",
    "OperatorEvent",
    "compose_replay_identifier",
    "decode_status_payload",
]
