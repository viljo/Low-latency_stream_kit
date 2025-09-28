"""Integration tests ensuring players consume telemetry directly from JetStream."""
from __future__ import annotations

import struct
from datetime import datetime, timezone

import pytest

from tspi_kit import InMemoryJetStream, TSPIProducer
from tspi_kit.receiver import TSPIReceiver
from tspi_kit.ui import PlayerState, UiConfig


def _build_geocentric_datagram(
    *,
    sensor_id: int,
    day: int,
    time_s: float,
    position: tuple[float, float, float],
    velocity: tuple[float, float, float],
    acceleration: tuple[float, float, float],
) -> bytes:
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


@pytest.mark.qt_no_exception_capture
def test_player_state_switches_sources_and_scrubs(qtbot) -> None:
    stream = InMemoryJetStream()
    live_producer = TSPIProducer(stream)
    historical_producer = TSPIProducer(stream, stream_prefix="player.training.playout")

    base_epoch = 1_700_000_700.0
    for index in range(3):
        datagram = _build_geocentric_datagram(
            sensor_id=700,
            day=12,
            time_s=0.25 + index * 0.25,
            position=(100.0 + index, 200.0, 300.0),
            velocity=(10.0, -5.0, 0.5),
            acceleration=(0.1, -0.1, 0.0),
        )
        live_producer.ingest(datagram, recv_time=base_epoch + index * 0.1)

    for index in range(2):
        datagram = _build_geocentric_datagram(
            sensor_id=701,
            day=12,
            time_s=5.0 + index,
            position=(400.0 + index, 500.0, 600.0),
            velocity=(0.0, 0.0, 0.0),
            acceleration=(0.0, 0.0, 0.0),
        )
        historical_producer.ingest(datagram, recv_time=base_epoch + 10 + index)

    subjects = {
        "livestream": "tspi.>",
        "replay.default": "player.training.playout.>",
    }
    sources = {
        name: (lambda subject=subject: TSPIReceiver(stream.create_consumer(subject)))
        for name, subject in subjects.items()
    }

    state = PlayerState(sources, ui_config=UiConfig())
    try:
        state.preload(batch=10)
        assert state.buffer_size() == 3
        snapshot = state.buffer_snapshot()
        assert snapshot[0]["sensor_id"] == 700
        first_iso = snapshot[0]["recv_iso"]
        expected_iso = datetime.fromtimestamp(base_epoch, tz=timezone.utc).isoformat()
        assert first_iso == expected_iso
        assert datetime.fromisoformat(first_iso).timestamp() == pytest.approx(base_epoch)
        assert snapshot[0]["recv_epoch_ms"] == int(round(base_epoch * 1000))

        state.start()
        for _ in range(3):
            state.step_once()
        state.pause()

        assert state.buffer_size() == 0
        assert state.timeline_length() == 3
        assert state._metrics.frames == 3

        state.set_channel("replay.default")
        state.preload(batch=10)
        assert state.current_channel == "replay.default"
        assert state.buffer_size() == 2
        assert state.timeline_length() == 2

        state.start()
        state.step_once()
        state.pause()
        assert state.position() == 1

        state.scrub_to_index(0)
        assert state.position() == 0
        assert state.buffer_size() == 2

        state.seek(snapshot[0]["recv_iso"])
        assert state.position() == 0  # no matching ISO in historical dataset

        # Ensure live and historical subjects were published correctly.
        assert any(message.subject == "tspi.geocentric.700" for message in stream._messages)
        assert any(
            message.subject == "player.training.playout.geocentric.701" for message in stream._messages
        )
    finally:
        state.deleteLater()
