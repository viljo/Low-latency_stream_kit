"""Helpers for creating collaborative timeline tags."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Dict, Mapping, MutableMapping, Optional

import cbor2


TAG_BROADCAST_SUBJECT = "tags.broadcast"


def _normalise_timestamp(value: datetime | None) -> datetime:
    """Return a timezone-aware UTC timestamp."""

    if value is None:
        return datetime.now(tz=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(slots=True)
class TagPayload:
    """Structured representation of a timeline tag event."""

    id: str
    ts: str
    label: str
    status: str
    creator: str | None
    notes: str | None
    updated_ts: str
    extra: Mapping[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": self.id,
            "ts": self.ts,
            "label": self.label,
            "status": self.status,
            "updated_ts": self.updated_ts,
        }
        if self.creator:
            payload["creator"] = self.creator
        if self.notes:
            payload["notes"] = self.notes
        if self.extra:
            payload["extra"] = dict(self.extra)
        return payload


class TagSender:
    """Publish collaborative tag events to JetStream-compatible publishers."""

    def __init__(self, publisher, *, sender_id: str = "tag-ui") -> None:
        self._publisher = publisher
        self._sender_id = sender_id

    @property
    def sender_id(self) -> str:
        return self._sender_id

    def create_tag(
        self,
        comment: str,
        *,
        timestamp: datetime | None = None,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> TagPayload:
        """Publish a tag that annotates the supplied timestamp with a comment."""

        if not isinstance(comment, str) or not comment.strip():
            raise ValueError("Comment must be a non-empty string")

        ts = _normalise_timestamp(timestamp)
        iso = ts.isoformat()
        tag_id = str(uuid.uuid4())
        payload = TagPayload(
            id=tag_id,
            ts=iso,
            label=comment.strip(),
            status="active",
            creator=self._sender_id,
            notes=comment.strip(),
            updated_ts=iso,
            extra=dict(extra) if extra else {},
        )

        encoded = cbor2.dumps(payload.to_dict())
        headers: MutableMapping[str, str] = {"Nats-Msg-Id": tag_id}
        if self._sender_id:
            headers["X-Tag-Creator"] = self._sender_id
        published = self._publisher.publish(
            TAG_BROADCAST_SUBJECT,
            encoded,
            headers=headers,
            timestamp=time.time(),
        )
        if published is False:
            raise RuntimeError("Failed to publish tag event")
        return payload


__all__ = ["TagPayload", "TagSender", "TAG_BROADCAST_SUBJECT"]
