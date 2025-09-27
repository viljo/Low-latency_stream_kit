"""Archiver component that drains JetStream into the datastore."""
from __future__ import annotations

import cbor2

from .datastore import TimescaleDatastore


class Archiver:
    """Persist JetStream messages into the :class:`TimescaleDatastore`."""

    def __init__(self, jetstream, datastore: TimescaleDatastore) -> None:
        self._jetstream = jetstream
        self._datastore = datastore
        self._consumers = {
            "telemetry": jetstream.create_consumer("tspi.>"),
            "commands": jetstream.create_consumer("cmd.display.units"),
            "tags": jetstream.create_consumer("tags.>"),
        }

    def drain(self, batch_size: int = 50) -> int:
        """Consume and persist messages from all subscribed subjects."""

        stored = 0
        for kind, consumer in self._consumers.items():
            messages = consumer.pull(batch_size)
            for message in messages:
                payload = cbor2.loads(message.data)
                message_id = self._datastore.insert_message(
                    subject=message.subject,
                    kind=self._classify_kind(message.subject, kind),
                    payload=payload,
                    headers=message.headers,
                    published_ts=message.timestamp,
                    raw_cbor=message.data,
                )
                if message_id is None:
                    continue
                stored += 1
                if message.subject.startswith("cmd.display"):
                    self._datastore.upsert_command(
                        payload, message_id=message_id, published_ts=message.timestamp
                    )
                if message.subject.startswith("tags."):
                    self._datastore.apply_tag_event(
                        message.subject, payload, message_id=message_id
                    )
        return stored

    @staticmethod
    def _classify_kind(subject: str, default_kind: str) -> str:
        if subject.startswith("cmd.display"):
            return "command"
        if subject.startswith("tags."):
            return "tag"
        if subject.startswith("tspi.") or ".tspi." in subject:
            return "telemetry"
        return default_kind

