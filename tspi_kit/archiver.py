"""Archiver component that drains JetStream into TimescaleDB."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Dict

import cbor2

from .datastore import TimescaleDatastore


class Archiver:
    """Persist JetStream subjects into :class:`TimescaleDatastore`."""

    def __init__(
        self,
        jetstream,
        datastore: TimescaleDatastore,
        *,
        durable_prefix: str = "archiver",
        pull_timeout: float = 1.0,
    ) -> None:
        self._jetstream = jetstream
        self._datastore = datastore
        self._durable_prefix = durable_prefix
        self._pull_timeout = pull_timeout
        self._subscriptions: Dict[str, Any] = {}

    async def start(self) -> None:
        """Create pull subscriptions for telemetry, commands, and tags."""

        if self._subscriptions:
            return
        if hasattr(self._datastore, "connect"):
            connect = getattr(self._datastore, "connect")
            if callable(connect):
                await connect()
        self._subscriptions = {
            "telemetry": await self._jetstream.pull_subscribe(
                "tspi.>", durable=f"{self._durable_prefix}.telemetry"
            ),
            "commands": await self._jetstream.pull_subscribe(
                "cmd.display.units", durable=f"{self._durable_prefix}.commands"
            ),
            "tags": await self._jetstream.pull_subscribe(
                "tags.>", durable=f"{self._durable_prefix}.tags"
            ),
        }

    async def drain(self, batch_size: int = 50) -> int:
        """Consume available messages and persist them into TimescaleDB."""

        if not self._subscriptions:
            await self.start()

        stored = 0
        for kind, subscription in self._subscriptions.items():
            messages = await subscription.fetch(batch_size, timeout=self._pull_timeout)
            for message in messages:
                payload = cbor2.loads(message.data)
                timestamp = self._message_timestamp(message)
                message_id = await self._datastore.insert_message(
                    subject=message.subject,
                    kind=self._classify_kind(message.subject, kind),
                    payload=payload,
                    headers=self._normalise_headers(message.headers),
                    published_ts=timestamp,
                    raw_cbor=message.data,
                )
                await message.ack()
                if message_id is None:
                    continue
                stored += 1
                if message.subject.startswith("cmd.display"):
                    await self._datastore.upsert_command(
                        payload, message_id=message_id, published_ts=timestamp
                    )
                if message.subject.startswith("tags."):
                    await self._datastore.apply_tag_event(
                        message.subject, payload, message_id=message_id
                    )
        return stored

    @staticmethod
    def _normalise_headers(headers) -> Dict[str, str]:
        if not headers:
            return {}
        if isinstance(headers, dict):
            return {str(key): str(value) for key, value in headers.items()}
        # nats.js.MsgHeaders implements .items()
        return {str(key): str(value) for key, value in headers.items()}

    @staticmethod
    def _message_timestamp(message) -> datetime:
        metadata = getattr(message, "metadata", None)
        if metadata and getattr(metadata, "timestamp", None):
            return metadata.timestamp
        return datetime.now(tz=UTC)

    @staticmethod
    def _classify_kind(subject: str, default_kind: str) -> str:
        if subject.startswith("cmd.display"):
            return "command"
        if subject.startswith("tags."):
            return "tag"
        if subject.startswith("tspi.") or ".tspi." in subject:
            return "telemetry"
        return default_kind

