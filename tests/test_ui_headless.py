"""Headless mode tests for Qt applications."""
from __future__ import annotations

import struct
from pathlib import Path

import dpkt
import pytest

try:  # pragma: no cover - exercised when PyQt5 unavailable
    from PyQt5 import QtCore  # noqa: F401 - imported for availability check
except ModuleNotFoundError:  # pragma: no cover - exercised when PyQt5 unavailable
    QtCore = None  # type: ignore[assignment]

pytestmark = pytest.mark.skipif(QtCore is None, reason="PyQt5 not installed")

if QtCore is not None:
    from tspi_kit import TSPIProducer
    from tspi_kit.ui import HeadlessPlayerRunner, UiConfig
    from tspi_kit.ui.player import connect_in_memory
    from tspi_kit.ui.pcap_player import build_headless_player_from_pcap
    from tspi_kit.ui.generator import build_headless_generator


def _geocentric_datagram(sensor_id: int, time_ticks: int) -> bytes:
    header = struct.pack(
        ">BBHHIBH",
        0xC1,
        4,
        sensor_id,
        120,
        time_ticks,
        0xFF,
        0x01,
    )
    payload = struct.pack(
        ">iii hhh hhh".replace(" ", ""),
        1000,
        2000,
        3000,
        100,
        50,
        20,
        10,
        5,
        2,
    )
    return header + payload


def test_headless_player_emits_metrics(capsys):
    stream, receiver = connect_in_memory()
    producer = TSPIProducer(stream)
    for index in range(5):
        producer.ingest(_geocentric_datagram(500 + index, 10_000 + index * 100), recv_time=1_700_000_000.0 + index)
    runner = HeadlessPlayerRunner(
        receiver,
        ui_config=UiConfig(metrics_interval=0.0),
        duration=0.1,
        stdout_json=True,
    )
    runner.run()
    out = capsys.readouterr().out
    assert "frames" in out


def test_pcap_headless_pipeline(tmp_path: Path):
    datagram = _geocentric_datagram(600, 11_000)
    pcap_path = tmp_path / "capture.pcap"
    with pcap_path.open("wb") as handle:
        writer = dpkt.pcap.Writer(handle, linktype=147)
        writer.writepkt(datagram, ts=0.0)
    runner = build_headless_player_from_pcap(pcap_path, rate=2.0)
    runner.run()


def test_generator_headless_pipeline():
    runner = build_headless_generator(count=3, rate=5.0, duration=0.2)
    runner.run()