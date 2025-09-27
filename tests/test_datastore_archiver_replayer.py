"""Tests covering the persistence and replay stack."""
from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from typing import Any, Dict, List, Sequence

import cbor2
import pytest

from tspi_kit import (
    Archiver,
    COMMAND_SUBJECT_PREFIX,
    StoreReplayer,
    TSPIProducer,
)
from tspi_kit.datastore import MessageRecord, TagRecord


def _match_token(subject_token: str, pattern_token: str) -> bool:
    if pattern_token == ">":
        return True
    if pattern_token == "*":
        return True
    return subject_token == pattern_token


def _match_subject(subject: str, pattern: str) -> bool:
    if pattern == ">":
        return True

    subject_tokens = subject.split(".")
    pattern_tokens = pattern.split(".")

    for index, token in enumerate(pattern_tokens):
        if token == ">":
            return True
        if index >= len(subject_tokens):
            return False
        if not _match_token(subject_tokens[index], token):
            return False

    return len(subject_tokens) == len(pattern_tokens)


@dataclass
class _FakeMetadata:
    timestamp: datetime


class _FakeMsg:
    def __init__(self, subject: str, data: bytes, headers: Dict[str, str], timestamp: float) -> None:
        self.subject = subject
        self.data = data
        self.headers = headers
        self.metadata = _FakeMetadata(datetime.fromtimestamp(timestamp, tz=UTC))

    async def ack(self) -> None:
        return None


class _FakeSubscription:
    def __init__(self, broker: "_FakeJetStream", subject_filter: str) -> None:
        self._broker = broker
        self._filter = subject_filter
        self._cursor = 0

    async def fetch(self, batch: int, *, timeout: float | None = None) -> List[_FakeMsg]:  # noqa: ARG002 - parity with real API
        messages: List[_FakeMsg] = []
        while len(messages) < batch and self._cursor < len(self._broker.messages):
            candidate = self._broker.messages[self._cursor]
            self._cursor += 1
            if _match_subject(candidate.subject, self._filter):
                messages.append(candidate)
        return messages


class _FakeJetStream:
    def __init__(self) -> None:
        self.messages: List[_FakeMsg] = []
        self.replay_messages: List[_FakeMsg] = []

    async def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        headers: Dict[str, str] | None = None,
        timeout: float | None = None,  # noqa: ARG002 - parity with real API
        timestamp: float | None = None,  # noqa: ARG002 - compatibility with producer
    ) -> None:
        headers = headers or {}
        ts = timestamp if timestamp is not None else datetime.now(tz=UTC).timestamp()
        msg = _FakeMsg(subject, payload, dict(headers), timestamp=ts)
        if subject.startswith("player."):
            self.replay_messages.append(msg)
        else:
            self.messages.append(msg)

    async def pull_subscribe(self, subject: str, *, durable: str | None = None) -> _FakeSubscription:  # noqa: ARG002 - parity
        return _FakeSubscription(self, subject)


class _FakeTimescaleDatastore:
    def __init__(self) -> None:
        self._messages: List[MessageRecord] = []
        self._message_ids: Dict[str, int] = {}
        self._commands: Dict[str, Dict[str, Any]] = {}
        self._tags: Dict[str, TagRecord] = {}
        self._next_id = 1

    async def insert_message(
        self,
        *,
        subject: str,
        kind: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        published_ts: datetime,
        raw_cbor: bytes,
    ) -> int | None:
        message_id = headers.get("Nats-Msg-Id")
        if message_id and message_id in self._message_ids:
            return None

        record = MessageRecord(
            id=self._next_id,
            subject=subject,
            kind=kind,
            published_ts=published_ts.timestamp(),
            headers=dict(headers),
            payload=dict(payload),
            cbor=raw_cbor,
            recv_epoch_ms=payload.get("recv_epoch_ms"),
            recv_iso=payload.get("recv_iso"),
            message_type=payload.get("type"),
            sensor_id=payload.get("sensor_id"),
            day=payload.get("day"),
            time_s=payload.get("time_s"),
        )
        self._messages.append(record)
        if message_id:
            self._message_ids[message_id] = record.id
        self._next_id += 1
        return record.id

    async def fetch_messages_between(self, start_ts: float, end_ts: float) -> Sequence[MessageRecord]:
        return [
            record
            for record in self._messages
            if start_ts <= record.published_ts <= end_ts
        ]

    async def fetch_messages_for_tag(
        self, tag_id: str, *, window_seconds: float = 10.0
    ) -> Sequence[MessageRecord]:
        tag = self._tags.get(tag_id)
        if tag is None:
            return []
        centre = datetime.fromisoformat(tag.ts).timestamp()
        half_window = window_seconds / 2.0
        return await self.fetch_messages_between(centre - half_window, centre + half_window)

    async def upsert_command(
        self, payload: Dict[str, Any], *, message_id: int, published_ts: datetime
    ) -> None:
        cmd_id = payload.get("cmd_id")
        if not cmd_id:
            return
        enriched = dict(payload)
        enriched.setdefault("message_id", message_id)
        enriched.setdefault("published_ts", published_ts.isoformat())
        self._commands[cmd_id] = enriched

    async def latest_command(self, name: str) -> Dict[str, Any] | None:
        candidates = [cmd for cmd in self._commands.values() if cmd.get("name") == name]
        if not candidates:
            return None
        return sorted(candidates, key=lambda c: c["published_ts"], reverse=True)[0]

    async def apply_tag_event(
        self,
        subject: str,
        payload: Dict[str, Any],
        *,
        message_id: int,
    ) -> None:
        tag_id = payload.get("id")
        if not tag_id:
            return
        existing = self._tags.get(tag_id)
        base = dict(existing.__dict__) if existing else {}
        base.update(
            id=tag_id,
            ts=payload.get("ts", base.get("ts", datetime.now(tz=UTC).isoformat())),
            creator=payload.get("creator", base.get("creator")),
            label=payload.get("label", base.get("label")),
            category=payload.get("category", base.get("category")),
            notes=payload.get("notes", base.get("notes")),
            extra=payload.get("extra", base.get("extra", {})),
            status=base.get("status", "active"),
            updated_ts=payload.get("ts", base.get("updated_ts", datetime.now(tz=UTC).isoformat())),
        )
        if subject.endswith("delete"):
            base["status"] = "deleted"
        elif subject.endswith("broadcast"):
            base["status"] = payload.get("status", base.get("status", "active"))
        self._tags[tag_id] = TagRecord(**base)

    async def get_tag(self, tag_id: str) -> TagRecord | None:
        return self._tags.get(tag_id)

    async def list_tags(self, *, include_deleted: bool = False) -> Sequence[TagRecord]:
        tags = list(self._tags.values())
        if not include_deleted:
            tags = [tag for tag in tags if tag.status != "deleted"]
        return sorted(tags, key=lambda tag: tag.updated_ts, reverse=True)

    async def count_messages(self) -> int:
        return len(self._messages)

    async def count_commands(self) -> int:
        return len(self._commands)

    async def count_tags(self) -> int:
        return len(self._tags)


def _geocentric_datagram(sensor_id: int, day: int, time_s: float) -> bytes:
    time_ticks = int(round(time_s * 10_000))
    header = struct.pack(
        ">BBHHIBH",
        0xC1,
        4,
        sensor_id,
        day,
        time_ticks,
        0xFF,
        0x01,
    )
    payload = struct.pack(
        ">iii hhh hhh".replace(" ", ""),
        int(1000.0 * 100),
        int(2000.0 * 100),
        int(3000.0 * 100),
        int(10.0 * 100),
        int(5.0 * 100),
        int(-2.0 * 100),
        int(0.1 * 100),
        int(0.2 * 100),
        int(0.3 * 100),
    )
    return header + payload


def test_archiver_persists_messages_commands_and_tags() -> None:
    async def _exercise() -> None:
        jetstream = _FakeJetStream()
        datastore = _FakeTimescaleDatastore()
        producer = TSPIProducer(jetstream)
        archiver = Archiver(jetstream, datastore)

        base_epoch = 1_700_000_000.0
        datagram = _geocentric_datagram(42, 123, 10.0)
        await producer.ingest_async(datagram, recv_time=base_epoch)
        # duplicate publish should be ignored by datastore deduplication
        await producer.ingest_async(datagram, recv_time=base_epoch + 1)

        command_payload = {
            "cmd_id": "00000000-0000-0000-0000-000000000001",
            "name": "display.units",
            "ts": datetime.fromtimestamp(base_epoch, tz=timezone.utc).isoformat(),
            "sender": "ui-1",
            "payload": {"units": "metric"},
        }
        await jetstream.publish(
            f"{COMMAND_SUBJECT_PREFIX}.units",
            cbor2.dumps(command_payload),
            headers={"Nats-Msg-Id": command_payload["cmd_id"]},
            timestamp=base_epoch + 0.9,
        )

        marker_payload = {
            "cmd_id": "00000000-0000-0000-0000-000000000002",
            "name": "display.marker_color",
            "ts": datetime.fromtimestamp(base_epoch + 0.5, tz=timezone.utc).isoformat(),
            "sender": "ui-1",
            "payload": {"marker_color": "#ff00ff"},
        }
        await jetstream.publish(
            f"{COMMAND_SUBJECT_PREFIX}.marker_color",
            cbor2.dumps(marker_payload),
            headers={"Nats-Msg-Id": marker_payload["cmd_id"]},
        )

        tag_payload = {
            "id": "tag-1",
            "ts": datetime.fromtimestamp(base_epoch + 3, tz=timezone.utc).isoformat(),
            "creator": "tester",
            "label": "Target locked",
            "category": "event",
            "notes": "Lock achieved",
            "extra": {"confidence": 0.95},
        }
        await jetstream.publish(
            "tags.create",
            cbor2.dumps(tag_payload),
            headers={"Nats-Msg-Id": "tag-1"},
        )

        stored = await archiver.drain(batch_size=10)
        assert stored == 4
        assert await datastore.count_messages() == 4
        assert await datastore.count_commands() == 2
        assert await datastore.count_tags() == 1

        latest = await datastore.latest_command("display.units")
        assert latest is not None
        assert latest["payload"]["units"] == "metric"

        latest_marker = await datastore.latest_command("display.marker_color")
        assert latest_marker is not None
        assert latest_marker["payload"]["marker_color"] == "#ff00ff"

        tag_record = await datastore.get_tag("tag-1")
        assert tag_record is not None
        assert tag_record.label == "Target locked"
        assert tag_record.extra["confidence"] == pytest.approx(0.95)

        messages = await datastore.fetch_messages_between(base_epoch - 1, base_epoch + 4)
        telemetry = [m for m in messages if m.kind == "telemetry"]
        assert len(telemetry) == 1
        assert telemetry[0].payload["sensor_id"] == 42

    asyncio.run(_exercise())


def test_store_replayer_replays_with_pacing() -> None:
    async def _exercise() -> None:
        jetstream = _FakeJetStream()
        datastore = _FakeTimescaleDatastore()
        producer = TSPIProducer(jetstream)
        archiver = Archiver(jetstream, datastore)

        base_epoch = 1_700_100_000.0
        for index in range(3):
            datagram = _geocentric_datagram(55 + index, 200, 10.0 + index * 0.5)
            await producer.ingest_async(datagram, recv_time=base_epoch + index * 0.2)

        command_payload = {
            "cmd_id": "10000000-0000-0000-0000-000000000000",
            "name": "display.units",
            "ts": datetime.fromtimestamp(base_epoch + 0.9, tz=timezone.utc).isoformat(),
            "sender": "ui-test",
            "payload": {"units": "imperial"},
        }
        await jetstream.publish(
            f"{COMMAND_SUBJECT_PREFIX}.units",
            cbor2.dumps(command_payload),
            headers={"Nats-Msg-Id": command_payload["cmd_id"]},
            timestamp=base_epoch + 0.9,
        )

        await archiver.drain(batch_size=10)

        sleep_calls: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        replayer = StoreReplayer(datastore, jetstream, sleep=fake_sleep)
        start = base_epoch - 1
        end = base_epoch + 10
        messages = await replayer.replay_time_window("training", start, end)
        assert len(messages) == 4
        assert any(msg.kind == "command" for msg in messages)

        # The first message should not trigger a delay, subsequent ones honour recv_epoch_ms deltas.
        assert sleep_calls[0] == pytest.approx(0.2)
        assert sleep_calls[1] == pytest.approx(0.2)

        replayed_subjects = [msg.subject for msg in jetstream.replay_messages]
        assert len(replayed_subjects) == 4
        assert replayed_subjects[0] == "player.training.playout.geocentric.55"
        assert replayed_subjects[-1] == "player.training.playout.cmd.display.units"

    asyncio.run(_exercise())

