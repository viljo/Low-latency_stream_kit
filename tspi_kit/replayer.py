"""Store replayer implementation that republishes archived telemetry."""
from __future__ import annotations

from typing import Callable, Iterable, List
import time

from .datastore import MessageRecord, TimescaleDatastore


def _subject_for_replay(room: str, message: MessageRecord) -> str:
    prefix = f"player.{room}.playout"
    suffix = message.subject.split(".", 1)[-1]
    return f"{prefix}.{suffix}"


class StoreReplayer:
    """Replay historical telemetry from the :class:`TimescaleDatastore`."""

    def __init__(self, datastore: TimescaleDatastore, publisher, *, sleep: Callable[[float], None] | None = None) -> None:
        self._datastore = datastore
        self._publisher = publisher
        self._sleep = sleep or time.sleep

    def replay_time_window(
        self,
        room: str,
        start_ts: float,
        end_ts: float,
        *,
        pace: bool = True,
    ) -> List[MessageRecord]:
        messages = self._datastore.fetch_messages_between(start_ts, end_ts)
        self._replay(room, messages, pace=pace)
        return messages

    def replay_tag(
        self,
        room: str,
        tag_id: str,
        *,
        pace: bool = True,
        window_seconds: float = 10.0,
    ) -> List[MessageRecord]:
        messages = self._datastore.fetch_messages_for_tag(tag_id, window_seconds=window_seconds)
        self._replay(room, messages, pace=pace)
        return messages

    def _replay(self, room: str, messages: Iterable[MessageRecord], *, pace: bool) -> None:
        last_recv_ms: float | None = None
        last_time_s: float | None = None

        for record in messages:
            if pace:
                delay = self._compute_delay(record, last_recv_ms, last_time_s)
                if delay > 0:
                    self._sleep(delay)

            subject = _subject_for_replay(room, record)
            headers = dict(record.headers)
            message_id = headers.get("Nats-Msg-Id")
            if message_id is not None:
                headers["Nats-Msg-Id"] = f"{message_id}:replay:{room}"
            headers.setdefault("X-Replay-Origin", "datastore")
            self._publisher.publish(
                subject,
                record.cbor,
                headers=headers,
                timestamp=record.published_ts,
            )

            last_recv_ms = record.recv_epoch_ms
            last_time_s = record.time_s

    @staticmethod
    def _compute_delay(
        record: MessageRecord,
        last_recv_ms: float | None,
        last_time_s: float | None,
    ) -> float:
        if record.recv_epoch_ms is not None and last_recv_ms is not None:
            delta_ms = record.recv_epoch_ms - last_recv_ms
            if delta_ms > 0:
                return delta_ms / 1000.0

        if record.time_s is not None and last_time_s is not None:
            delta_s = record.time_s - last_time_s
            if delta_s > 0:
                return delta_s

        return 0.0

