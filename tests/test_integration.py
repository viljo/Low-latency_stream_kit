"""Integration tests exercising the Codex Spec Kit pipeline."""
from __future__ import annotations

import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import dpkt
import pytest

from tspi_kit import (
    FlightConfig,
    InMemoryJetStream,
    InMemoryJetStreamCluster,
    PCAPReplayer,
    TSPIFlightGenerator,
    TSPIProducer,
    TSPIReceiver,
)
from tspi_kit.datagrams import parse_tspi_datagram


def _build_geocentric_datagram(
    *,
    sensor_id: int,
    day: int,
    time_ticks: int,
    position: tuple[float, float, float],
    velocity: tuple[float, float, float],
    acceleration: tuple[float, float, float],
    status: int = 0xFF,
    status_flags: int = 0x01,
) -> bytes:
    import struct

    header = struct.pack(
        ">BBHHIBH",
        0xC1,
        4,
        sensor_id,
        day,
        time_ticks,
        status,
        status_flags,
    )
    payload = struct.pack(
        ">iii hhh hhh".replace(" ", ""),
        int(position[0] * 100),
        int(position[1] * 100),
        int(position[2] * 100),
        int(velocity[0] * 100),
        int(velocity[1] * 100),
        int(velocity[2] * 100),
        int(acceleration[0] * 100),
        int(acceleration[1] * 100),
        int(acceleration[2] * 100),
    )
    return header + payload


def _create_pipeline(jetstream=None):
    jetstream = jetstream or InMemoryJetStream()
    producer = TSPIProducer(jetstream)
    consumer = jetstream.create_consumer("tspi.>")
    receiver = TSPIReceiver(consumer)
    return jetstream, producer, receiver


def test_pcap_pipeline_round_trip() -> None:
    jetstream, producer, receiver = _create_pipeline()

    datagrams = [
        _build_geocentric_datagram(
            sensor_id=101,
            day=123,
            time_ticks=1000,
            position=(100.0, 200.0, 300.0),
            velocity=(10.0, -5.0, 0.5),
            acceleration=(0.1, -0.1, 0.0),
        ),
        _build_geocentric_datagram(
            sensor_id=102,
            day=123,
            time_ticks=1500,
            position=(150.0, 250.0, 350.0),
            velocity=(12.0, -4.0, 0.6),
            acceleration=(0.2, -0.2, 0.1),
        ),
    ]

    with tempfile.NamedTemporaryFile(suffix=".pcap", delete=False) as handle:
        writer = dpkt.pcap.Writer(handle, linktype=147)
        writer.writepkt(b"invalid", ts=0.0)
        for index, datagram in enumerate(datagrams):
            writer.writepkt(datagram, ts=float(index) * 0.5)
        path = Path(handle.name)

    replayer = PCAPReplayer(path)
    base_epoch = 1_700_000_000.0
    replayer.replay(producer, rate=2.0, base_epoch=base_epoch)

    try:
        messages = receiver.fetch_all()
        assert len(messages) == len(datagrams)

        for index, payload in enumerate(messages):
            assert payload["type"] == "geocentric"
            assert payload["sensor_id"] == 101 + index
            assert payload["payload"]["x_m"] == pytest.approx(100.0 + 50.0 * index)

            delta_capture = index * 0.5
            expected_recv = base_epoch + delta_capture / 2.0
            expected_ms = int(round(expected_recv * 1000))
            assert payload["recv_epoch_ms"] == expected_ms
            iso = datetime.fromtimestamp(expected_recv, tz=timezone.utc).isoformat()
            assert payload["recv_iso"] == iso
    finally:
        path.unlink(missing_ok=True)


def test_generator_pipeline_throughput_and_ranges() -> None:
    jetstream, producer, receiver = _create_pipeline()
    config = FlightConfig(count=5, rate_hz=20.0, speed_min_mps=60.0, speed_max_mps=120.0)
    generator = TSPIFlightGenerator(config)

    generator.stream_to_producer(producer, duration_seconds=0.5, base_epoch=1_700_000_100.0)

    messages = receiver.fetch_all()
    assert len(messages) == int(config.count * config.rate_hz * 0.5)

    magnitudes = [
        math.sqrt(
            m["payload"]["vx_mps"] ** 2
            + m["payload"]["vy_mps"] ** 2
            + m["payload"]["vz_mps"] ** 2
        )
        for m in messages
    ]
    assert min(magnitudes) >= config.speed_min_mps - 1
    assert max(magnitudes) <= config.speed_max_mps + 1

    time_values = [m["time_s"] for m in messages]
    assert all(time_values[i] <= time_values[i + 1] for i in range(len(time_values) - 1))


def test_generator_airshow_style_profiles() -> None:
    config = FlightConfig(count=4, rate_hz=10.0, style="airshow")
    generator = TSPIFlightGenerator(config)

    datagrams = list(generator.generate(5))
    assert len(datagrams) == config.count * 5

    parsed = [parse_tspi_datagram(packet) for packet, _ in datagrams]
    speeds = [
        (p.payload["vx_mps"] ** 2 + p.payload["vy_mps"] ** 2 + p.payload["vz_mps"] ** 2) ** 0.5
        for p in parsed
    ]
    assert min(speeds) >= config.speed_min_mps - 1
    assert max(speeds) <= config.speed_max_mps + 5

    altitudes = [p.payload["z_m"] for p in parsed]
    assert max(altitudes) - min(altitudes) > 100

def test_deduplication_enforced() -> None:
    jetstream, producer, receiver = _create_pipeline()
    datagram = _build_geocentric_datagram(
        sensor_id=301,
        day=200,
        time_ticks=5000,
        position=(1.0, 2.0, 3.0),
        velocity=(0.1, 0.2, 0.3),
        acceleration=(0.01, 0.02, 0.03),
    )

    first = producer.ingest(datagram, recv_time=1_700_000_200.0)
    second = producer.ingest(datagram, recv_time=1_700_000_201.0)

    messages = receiver.fetch_all()
    assert len(messages) == 1
    assert messages[0]["sensor_id"] == 301


def test_replay_seek_monotonicity() -> None:
    jetstream, producer, receiver = _create_pipeline()

    for index in range(5):
        datagram = _build_geocentric_datagram(
            sensor_id=400 + index,
            day=50,
            time_ticks=1_000 + index * 250,
            position=(0.0, 0.0, 0.0),
            velocity=(10.0, 0.0, 0.0),
            acceleration=(0.0, 0.0, 0.0),
        )
        producer.ingest(datagram, recv_time=1_700_000_300.0 + index)

    messages = receiver.fetch_all()
    seek_start = 0.15
    replay_window = [m for m in messages if m["time_s"] >= seek_start]
    assert replay_window
    assert replay_window[0]["time_s"] >= seek_start
    assert [m["time_s"] for m in replay_window] == sorted(m["time_s"] for m in replay_window)


def test_cluster_failover_continuity() -> None:
    cluster = InMemoryJetStreamCluster(replicas=3)
    producer = TSPIProducer(cluster)
    consumer = cluster.create_consumer("tspi.>")
    receiver = TSPIReceiver(consumer)

    for index in range(3):
        datagram = _build_geocentric_datagram(
            sensor_id=600 + index,
            day=10,
            time_ticks=2_000 + index * 100,
            position=(index * 10.0, 0.0, 1000.0),
            velocity=(50.0, 0.0, 0.0),
            acceleration=(0.0, 0.0, 0.0),
        )
        producer.ingest(datagram, recv_time=1_700_000_400.0 + index)

    cluster.kill_leader()

    for index in range(3, 6):
        datagram = _build_geocentric_datagram(
            sensor_id=600 + index,
            day=10,
            time_ticks=2_000 + index * 100,
            position=(index * 10.0, 0.0, 1000.0),
            velocity=(50.0, 0.0, 0.0),
            acceleration=(0.0, 0.0, 0.0),
        )
        producer.ingest(datagram, recv_time=1_700_000_400.0 + index)

    messages = receiver.fetch_all()
    assert len(messages) == 6
    assert messages[0]["sensor_id"] == 600
    assert messages[-1]["sensor_id"] == 605


def test_back_pressure_metrics() -> None:
    jetstream = InMemoryJetStream()
    producer = TSPIProducer(jetstream)
    consumer = jetstream.create_consumer("tspi.>")

    for index in range(20):
        updated = _build_geocentric_datagram(
            sensor_id=900,
            day=5,
            time_ticks=10_000 + index * 10_000,
            position=(0.0, 0.0, 1000.0),
            velocity=(5.0, 0.0, 0.0),
            acceleration=(0.0, 0.0, 0.0),
        )
        producer.ingest(updated, recv_time=1_700_000_500.0 + index)

    assert consumer.pending() == 20

    receiver = TSPIReceiver(consumer)
    receiver.fetch(batch=5)
    assert consumer.pending() == 15
