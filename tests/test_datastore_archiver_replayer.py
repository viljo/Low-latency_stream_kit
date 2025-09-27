"""Tests for the Timescale datastore, Archiver, and Store Replayer."""
from __future__ import annotations

import struct
from datetime import datetime, timezone

import cbor2
import pytest

from tspi_kit import (
    Archiver,
    InMemoryJetStream,
    StoreReplayer,
    TSPIProducer,
    TimescaleDatastore,
)


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
    jetstream = InMemoryJetStream()
    datastore = TimescaleDatastore()
    producer = TSPIProducer(jetstream)
    archiver = Archiver(jetstream, datastore)

    base_epoch = 1_700_000_000.0
    datagram = _geocentric_datagram(42, 123, 10.0)
    producer.ingest(datagram, recv_time=base_epoch)
    # duplicate publish should be ignored by datastore deduplication
    producer.ingest(datagram, recv_time=base_epoch + 1)

    command_payload = {
        "cmd_id": "00000000-0000-0000-0000-000000000001",
        "name": "display.units",
        "ts": datetime.fromtimestamp(base_epoch, tz=timezone.utc).isoformat(),
        "sender": "ui-1",
        "payload": {"units": "metric"},
    }
    jetstream.publish(
        "cmd.display.units",
        cbor2.dumps(command_payload),
        headers={"Nats-Msg-Id": command_payload["cmd_id"]},
        timestamp=base_epoch + 2,
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
    jetstream.publish(
        "tags.create",
        cbor2.dumps(tag_payload),
        headers={"Nats-Msg-Id": "tag-1"},
        timestamp=base_epoch + 3,
    )

    stored = archiver.drain(batch_size=10)
    assert stored == 3
    assert datastore.count_messages() == 3
    assert datastore.count_commands() == 1
    assert datastore.count_tags() == 1

    latest = datastore.latest_command("display.units")
    assert latest is not None
    assert latest["payload"]["units"] == "metric"

    tag_record = datastore.get_tag("tag-1")
    assert tag_record is not None
    assert tag_record.label == "Target locked"
    assert tag_record.extra["confidence"] == pytest.approx(0.95)

    messages = datastore.fetch_messages_between(base_epoch - 1, base_epoch + 4)
    telemetry = [m for m in messages if m.kind == "telemetry"]
    assert len(telemetry) == 1
    assert telemetry[0].payload["sensor_id"] == 42


def test_store_replayer_replays_with_pacing() -> None:
    jetstream = InMemoryJetStream()
    datastore = TimescaleDatastore()
    producer = TSPIProducer(jetstream)
    archiver = Archiver(jetstream, datastore)

    base_epoch = 1_700_100_000.0
    for index in range(3):
        datagram = _geocentric_datagram(55 + index, 200, 10.0 + index * 0.5)
        producer.ingest(datagram, recv_time=base_epoch + index * 0.2)

    archiver.drain(batch_size=10)

    sleep_calls: list[float] = []

    def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    replayer = StoreReplayer(datastore, jetstream, sleep=fake_sleep)
    start = base_epoch - 1
    end = base_epoch + 10
    messages = replayer.replay_time_window("training", start, end)
    assert len(messages) == 3

    # The first message should not trigger a delay, subsequent ones should honour recv_epoch_ms deltas.
    assert sleep_calls[0] == pytest.approx(0.2)
    assert sleep_calls[1] == pytest.approx(0.2)

    replayed_subjects = [m.subject for m in jetstream._messages if m.subject.startswith("player.training.playout")]
    assert len(replayed_subjects) == 3
    assert replayed_subjects[0] == "player.training.playout.geocentric.55"

