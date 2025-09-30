"""UI tests for the JetStream player."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import struct

import pytest
import cbor2

from tspi_kit import CommandSender, InMemoryJetStream, TSPIProducer
from tspi_kit.receiver import TSPIReceiver
from tspi_kit.ui import JetStreamPlayerWindow, PlayerState, UiConfig
from tspi_kit.ui.player import connect_in_memory


@pytest.fixture
def populated_player():
    stream = InMemoryJetStream()
    producer = TSPIProducer(stream)
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    payloads = []
    header_fmt = ">BBHHIBH"
    payload_fmt = ">iii hhh hhh".replace(" ", "")
    for index in range(5):
        header = struct.pack(
            header_fmt,
            0xC1,
            4,
            100 + index,
            200,
            10_000 + index * 500,
            0xFF,
            0x01,
        )
        payload = struct.pack(
            payload_fmt,
            int(100.0 * 1),
            int(200.0 * 1),
            int(300.0 * 1),
            int(10.0 * 100),
            int(5.0 * 100),
            int(2.0 * 100),
            int(0.5 * 100),
            int(0.25 * 100),
            int(0.1 * 100),
        )
        datagram = header + payload
        recv_time = base.timestamp() + index * 0.1
        payloads.append(producer.ingest(datagram, recv_time=recv_time))
    consumer = stream.create_consumer(">")
    receiver = TSPIReceiver(consumer)
    window = JetStreamPlayerWindow(receiver)
    window.state.preload()
    return window, payloads


def test_player_defaults_to_livestream_channel(populated_player):
    window, _ = populated_player
    assert window.state.current_channel == "livestream"
    assert window.source_combo.currentText() == "livestream"


def test_player_state_normalises_channel_aliases():
    stream, sources = connect_in_memory({"live": "tspi.>"})
    state = PlayerState(sources, ui_config=UiConfig())
    assert state.current_channel == "livestream"
    assert "livestream" in state.available_channels


def test_player_controls_toggle(populated_player):
    window, payloads = populated_player
    assert not window.state.playing
    window.play_button.click()
    assert window.state.playing
    window.step_once()
    window.play_button.click()
    assert not window.state.playing


def test_seek_and_rate(populated_player):
    window, payloads = populated_player
    window.play_button.click()
    window.step_once()
    buffer_before = window.state.buffer_size()
    last_iso = payloads[-1]["recv_iso"]
    window.seek_input.setText(last_iso)
    window.seek_button.click()
    assert window.state.buffer_size() <= buffer_before
    window.rate_spin.setValue(2.0)
    assert window.state.rate == pytest.approx(2.0)


def test_map_smoothing(populated_player):
    window, payloads = populated_player
    window.play_button.click()
    first_state = window.map_widget.state
    window.step_once()
    second_state = window.map_widget.state
    assert second_state.center != first_state.center
    window.step_once()
    third_state = window.map_widget.state
    delta_first = abs(second_state.center[0] - first_state.center[0])
    delta_second = abs(third_state.center[0] - second_state.center[0])
    assert delta_second < delta_first


def test_player_applies_display_commands():
    stream = InMemoryJetStream()
    producer = TSPIProducer(stream)
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    header = struct.pack(
        ">BBHHIBH",
        0xC1,
        4,
        501,
        200,
        10_000,
        0xFF,
        0x01,
    )
    payload = struct.pack(
        ">iii hhh hhh".replace(" ", ""),
        int(100.0 * 1),
        int(200.0 * 1),
        int(300.0 * 1),
        int(10.0 * 100),
        int(5.0 * 100),
        int(2.0 * 100),
        int(0.5 * 100),
        int(0.25 * 100),
        int(0.1 * 100),
    )
    producer.ingest(header + payload, recv_time=base.timestamp())
    sender = CommandSender(stream, sender_id="test-ui")
    sender.send_units("imperial")
    sender.send_marker_color("#123456")
    consumer = stream.create_consumer(">")
    receiver = TSPIReceiver(consumer)
    window = JetStreamPlayerWindow(receiver)
    window.state.preload(batch=10)
    window.play_button.click()
    for _ in range(window.state.timeline_length()):
        window.step_once()
    assert window.state.display_units == "imperial"
    assert window.state.marker_color == "#123456"
    assert window.map_widget.marker_color == "#123456"
    assert "imperial" in window._units_label.text()
    assert "#123456" in window._marker_label.text()


def test_player_handles_tag_events():
    stream = InMemoryJetStream()
    producer = TSPIProducer(stream)
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    header = struct.pack(
        ">BBHHIBH",
        0xC1,
        4,
        700,
        200,
        10_000,
        0xFF,
        0x01,
    )
    payload = struct.pack(
        ">iii hhh hhh".replace(" ", ""),
        int(100.0 * 1),
        int(200.0 * 1),
        int(300.0 * 1),
        int(10.0 * 100),
        int(5.0 * 100),
        int(2.0 * 100),
        int(0.5 * 100),
        int(0.25 * 100),
        int(0.1 * 100),
    )
    producer.ingest(header + payload, recv_time=base.timestamp())

    tag_payload = {
        "id": "tag-42",
        "ts": base.isoformat(),
        "label": "POI",
        "status": "active",
    }
    stream.publish("tags.test.created", cbor2.dumps(tag_payload))

    consumer = stream.create_consumer(">")
    receiver = TSPIReceiver(consumer)
    window = JetStreamPlayerWindow(receiver)

    received: list[dict] = []
    window.state.tag_event.connect(received.append)

    window.state.preload(batch=10)
    window.state.start()
    while window.state.position() < window.state.timeline_length():
        window.step_once()

    assert received and received[0]["id"] == "tag-42"
    assert "tag-42" in window.state.tags
    assert window._tag_list.count() == 1
    assert "POI" in window._tag_list.item(0).text()

    update_payload = dict(tag_payload)
    update_payload["label"] = "POI updated"
    update_payload["updated_ts"] = (base + timedelta(minutes=5)).isoformat()
    stream.publish("tags.test.updated", cbor2.dumps(update_payload))
    window.state.preload(batch=10)
    while window.state.position() < window.state.timeline_length():
        window.step_once()

    tag_state = window.state.tags["tag-42"]
    assert tag_state["ts"] == tag_payload["ts"]
    assert tag_state["updated_ts"] == update_payload["updated_ts"]
    assert tag_state["updated_ts"] != tag_state["ts"]
    assert received[-1]["updated_ts"] == update_payload["updated_ts"]
    assert "POI updated" in window._tag_list.item(0).text()

    window.state.scrub_to_index(window.state.timeline_length() - 1)
    assert window.state.seek_to_tag("tag-42")
    snapshot = window.state.buffer_snapshot()
    assert snapshot and snapshot[0]["id"] == "tag-42"

    deletion = dict(tag_payload)
    deletion["status"] = "deleted"
    stream.publish("tags.test.deleted", cbor2.dumps(deletion))
    window.state.preload(batch=10)
    while window.state.position() < window.state.timeline_length():
        window.step_once()

    assert "tag-42" not in window.state.tags
    assert window._tag_list.count() == 0


def test_forward_jump_replays_commands_and_tags():
    stream = InMemoryJetStream()
    producer = TSPIProducer(stream)
    sender = CommandSender(stream, sender_id="test-ui")
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)

    header_fmt = ">BBHHIBH"
    payload_fmt = ">iii hhh hhh".replace(" ", "")

    def build_datagram(sequence: int) -> bytes:
        header = struct.pack(
            header_fmt,
            0xC1,
            4,
            900,
            200,
            10_000 + sequence * 500,
            0xFF,
            0x01,
        )
        payload = struct.pack(
            payload_fmt,
            int(100.0 * 1),
            int(200.0 * 1),
            int(300.0 * 1),
            int(10.0 * 100),
            int(5.0 * 100),
            int(2.0 * 100),
            int(0.5 * 100),
            int(0.25 * 100),
            int(0.1 * 100),
        )
        return header + payload

    producer.ingest(build_datagram(0), recv_time=base.timestamp())
    command_payload = sender.send_units("imperial")

    tag_payload = {
        "id": "tag-forward",
        "ts": (base.replace(hour=1)).isoformat(),
        "label": "Forward jump",
        "status": "active",
    }
    stream.publish("tags.broadcast", cbor2.dumps(tag_payload))

    producer.ingest(build_datagram(1), recv_time=base.timestamp() + 1)

    consumer = stream.create_consumer(">")
    receiver = TSPIReceiver(consumer)
    window = JetStreamPlayerWindow(receiver)

    commands: list[dict] = []
    tags: list[dict] = []
    window.state.command_event.connect(commands.append)
    window.state.tag_event.connect(tags.append)

    window.state.preload(batch=10)

    # Jump forward before playback begins to prime state.
    window.state.scrub_to_index(window.state.timeline_length() - 1)
    assert commands and commands[-1]["cmd_id"] == command_payload.cmd_id
    assert tags and tags[-1]["id"] == tag_payload["id"]

    commands.clear()
    tags.clear()

    # Return to the start, process the first telemetry frame, then skip ahead again.
    window.state.scrub_to_index(0)
    window.state.start()
    window.step_once()
    window.state.pause()
    window.state.scrub_to_index(window.state.timeline_length() - 1)

    assert commands and commands[-1]["cmd_id"] == command_payload.cmd_id
    assert tags and tags[-1]["id"] == tag_payload["id"]
