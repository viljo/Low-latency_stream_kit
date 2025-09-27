"""UI tests for the JetStream player."""
from __future__ import annotations

from datetime import datetime, timezone
import struct

import pytest
from PyQt5 import QtCore

from tspi_kit import CommandSender, InMemoryJetStream, TSPIProducer
from tspi_kit.receiver import TSPIReceiver
from tspi_kit.ui import JetStreamPlayerWindow


@pytest.fixture
def populated_player(qtbot):
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
    consumer = stream.create_consumer("tspi.>")
    receiver = TSPIReceiver(consumer)
    window = JetStreamPlayerWindow(receiver)
    qtbot.addWidget(window)
    window.state.preload()
    return window, payloads


def test_player_controls_toggle(populated_player, qtbot):
    window, payloads = populated_player
    assert not window.state.playing
    qtbot.mouseClick(window.play_button, QtCore.Qt.LeftButton)
    assert window.state.playing
    window.step_once()
    qtbot.mouseClick(window.play_button, QtCore.Qt.LeftButton)
    assert not window.state.playing


def test_seek_and_rate(populated_player, qtbot):
    window, payloads = populated_player
    qtbot.mouseClick(window.play_button, QtCore.Qt.LeftButton)
    window.step_once()
    buffer_before = window.state.buffer_size()
    last_iso = payloads[-1]["recv_iso"]
    window.seek_input.setText(last_iso)
    qtbot.mouseClick(window.seek_button, QtCore.Qt.LeftButton)
    assert window.state.buffer_size() <= buffer_before
    window.rate_spin.setValue(2.0)
    assert window.state.rate == pytest.approx(2.0)


def test_map_smoothing(populated_player, qtbot):
    window, payloads = populated_player
    qtbot.mouseClick(window.play_button, QtCore.Qt.LeftButton)
    first_state = window.map_widget.state
    window.step_once()
    second_state = window.map_widget.state
    assert second_state.center != first_state.center
    window.step_once()
    third_state = window.map_widget.state
    delta_first = abs(second_state.center[0] - first_state.center[0])
    delta_second = abs(third_state.center[0] - second_state.center[0])
    assert delta_second < delta_first


def test_player_applies_display_commands(qtbot):
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
    consumer = stream.create_consumer("tspi.>")
    receiver = TSPIReceiver(consumer)
    window = JetStreamPlayerWindow(receiver)
    qtbot.addWidget(window)
    window.state.preload(batch=10)
    qtbot.mouseClick(window.play_button, QtCore.Qt.LeftButton)
    for _ in range(window.state.timeline_length()):
        window.step_once()
    assert window.state.display_units == "imperial"
    assert window.state.marker_color == "#123456"
    assert window.map_widget.marker_color == "#123456"
    assert "imperial" in window._units_label.text()
    assert "#123456" in window._marker_label.text()
