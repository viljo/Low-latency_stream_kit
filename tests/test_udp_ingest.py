"""Tests covering the standalone UDP producer helpers."""
from __future__ import annotations

import asyncio
from typing import Any

from tspi_kit import InMemoryJetStream
from tspi_kit.producer import TSPIProducer
from tspi_kit.udp_ingest import AsyncJetStreamPublisher, UDPIngestProtocol


def _geocentric_datagram(sensor_id: int = 101, day: int = 200, time_ticks: int = 10_000) -> bytes:
    import struct

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
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    return header + payload


def test_ingest_async_awaits_async_publisher() -> None:
    class Recorder:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bytes, dict[str, str] | None, float | None]] = []

        async def publish(
            self,
            subject: str,
            payload: bytes,
            *,
            headers: dict[str, str] | None = None,
            timestamp: float | None = None,
        ) -> None:
            await asyncio.sleep(0)
            self.calls.append((subject, payload, headers, timestamp))

    publisher = Recorder()
    producer = TSPIProducer(publisher)
    datagram = _geocentric_datagram(sensor_id=42, time_ticks=12_345)

    async def _exercise() -> None:
        payload = await producer.ingest_async(datagram, recv_time=1_700_000_000.0)

        assert payload is not None
        assert publisher.calls
        subject, encoded, headers, timestamp = publisher.calls[0]
        assert subject == "tspi.geocentric.42"
        assert headers and "Nats-Msg-Id" in headers
        assert timestamp == 1_700_000_000.0
        assert payload["sensor_id"] == 42

    asyncio.run(_exercise())


def test_ingest_filters_sensor_ids() -> None:
    stream = InMemoryJetStream()
    producer = TSPIProducer(stream, allowed_sensors={123})

    datagram = _geocentric_datagram(sensor_id=456)
    result = producer.ingest(datagram, recv_time=1_700_000_500.0)

    assert result is None
    consumer = stream.create_consumer(">")
    assert consumer.pull(1) == []


def test_udp_protocol_dispatches_to_producer() -> None:
    calls: list[bytes] = []

    class DummyProducer:
        async def ingest_async(self, datagram: bytes, *, recv_time: float | None = None) -> None:
            calls.append(datagram)

    async def _exercise() -> None:
        protocol = UDPIngestProtocol(DummyProducer())
        protocol.datagram_received(b"packet", ("127.0.0.1", 12345))

        await asyncio.sleep(0)
        assert calls == [b"packet"]
        await protocol.drain()

    asyncio.run(_exercise())


def test_async_publisher_passes_timeout() -> None:
    class FakeJetStream:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        async def publish(self, subject: str, payload: bytes, *, headers=None, timeout=None) -> None:
            self.calls.append((subject, payload, headers, timeout))

    jetstream = FakeJetStream()
    publisher = AsyncJetStreamPublisher(jetstream, publish_timeout=3.5)

    async def _exercise() -> None:
        await publisher.publish("tspi.test", b"data", headers={"X": "1"})

    asyncio.run(_exercise())

    assert jetstream.calls == [("tspi.test", b"data", {"X": "1"}, 3.5)]
